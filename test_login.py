import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
import asyncio, builtins, importlib.util, os, sys, tempfile, types

class E:                                   # поддельные ошибки Telethon
    class SessionPasswordNeededError(Exception): pass
    class PhoneCodeInvalidError(Exception): pass
    class PhoneCodeExpiredError(Exception): pass
class ApiIdInvalidError(Exception): pass
E.ApiIdInvalidError = ApiIdInvalidError
class FloodWaitError(Exception):
    def __init__(self, seconds): self.seconds = seconds; super().__init__(f"A wait of {seconds} seconds is required")
E.FloodWaitError = FloodWaitError
tele = types.ModuleType("telethon"); tele.TelegramClient = object; tele.errors = E
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = E
os.chdir(tempfile.mkdtemp())
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
bot.PHONE = "+79990000000"

class SentApp:  # SentCodeTypeApp
    pass
SentApp.__name__ = "SentCodeTypeApp"
class SentSms: pass
SentSms.__name__ = "SentCodeTypeSms"

class FakeClient:
    def __init__(self, good="12345", password=None, flood=None, via_app=True):
        self.good, self.password, self.flood = good, password, flood
        self.authorized, self.sent = False, []
        self.via_app = via_app
    async def send_code_request(self, phone, force_sms=False):
        # как настоящий telethon: повторный вызов для того же номера = «прислать другим способом»
        if self.flood: raise FloodWaitError(self.flood)
        resend = bool(self.sent)
        self.sent.append(resend)
        return types.SimpleNamespace(type=SentSms() if (resend or not self.via_app) else SentApp())
    async def sign_in(self, phone=None, code=None, password=None):
        if password is not None:
            if password != self.password: raise Exception("bad password")
            self.authorized = True; return
        if code == "00000": raise E.PhoneCodeExpiredError()
        if code != self.good: raise E.PhoneCodeInvalidError()
        if self.password: raise E.SessionPasswordNeededError()
        self.authorized = True
    async def is_user_authorized(self): return self.authorized
    async def qr_login(self):
        c = self
        class QR:
            url = "tg://login?token=abc"
            async def wait(self, timeout=None): c.authorized = True
        return QR()

def feed(*answers):
    it = iter(answers)
    builtins.input = lambda prompt="": next(it)
    return it

def run(client):
    return asyncio.run(bot.telegram_login(client))

# 1) обычный вход по коду (код пришёл в приложение)
c = FakeClient(); feed("1", "12345")
assert run(c) is c and c.authorized
print("l1 ok: вход по коду")

# 2) неверный код, потом верный; пробелы в коде не мешают
c = FakeClient(); feed("1", "99999", "1 2 3 4 5")
assert run(c) is c and c.authorized
print("l2 ok: неверный код -> повтор -> вход")

# 3) код не пришёл: «r» -> повторная отправка другим способом (SMS)
c = FakeClient(); feed("1", "r", "12345")
assert run(c) is c and c.authorized and c.sent == [False, True], c.sent
print("l3 ok: r — повторная отправка (SMS), вход")

# 4) устаревший код -> новый запрос
c = FakeClient(); feed("1", "00000", "12345")
assert run(c) is c and c.authorized and len(c.sent) == 2
print("l4 ok: устаревший код -> запросил новый -> вход")

# 5) двухэтапная проверка
c = FakeClient(password="pass"); feed("1", "12345", "pass")
assert run(c) is c and c.authorized
print("l5 ok: пароль двухэтапной проверки")

# 6) Telegram просит подождать -> понятное сообщение, дальше выбор «без входа»
c = FakeClient(flood=86400)
if os.path.exists(bot.WEB_FLAG): os.remove(bot.WEB_FLAG)
out = []
orig_print = builtins.print
builtins.print = lambda *a, **k: out.append(" ".join(map(str, a)))
feed("1", "3")
r = run(c)
builtins.print = orig_print
assert isinstance(r, bot.WebClient) and os.path.exists(bot.WEB_FLAG)
assert any("подождать 24 ч" in l for l in out), out[-6:]
print("l6 ok: FloodWait -> сколько ждать, переход на режим без входа")

# 7) вход по ссылке
c = FakeClient(); feed("2")
assert run(c) is c and c.authorized
print("l7 ok: вход по ссылке")

# 8) сразу без входа
if os.path.exists(bot.WEB_FLAG): os.remove(bot.WEB_FLAG)
feed("3")
assert isinstance(run(FakeClient()), bot.WebClient)
print("l8 ok: «3» — без входа")
# 9) неверные api_id/api_hash -> объяснение, что делать на my.telegram.org
class BadApi(FakeClient):
    async def send_code_request(self, phone, force_sms=False): raise ApiIdInvalidError("api_id/api_hash invalid")
out = []
builtins.print = lambda *a, **k: out.append(" ".join(map(str, a)))
feed("1", "3")
run(BadApi())
builtins.print = orig_print
assert any("my.telegram.org" in l and "config.json" in l for l in out), out
print("l9 ok: неверные api_id/api_hash — объясняет, что сделать на my.telegram.org")

def capture(client, *answers):
    if os.path.exists(bot.WEB_FLAG): os.remove(bot.WEB_FLAG)
    out = []
    builtins.print = lambda *a, **k: out.append(" ".join(map(str, a)))
    feed(*answers)
    try:
        r = run(client)
    finally:
        builtins.print = orig_print
    return r, "\n".join(out)

def named(name, **kw):
    cls = type(name, (), {}); obj = cls(); obj.__dict__.update(kw); return obj

# 10) аккаунт нигде не открыт, SMS платные: telethon падает AttributeError -> понятное объяснение, без входа
class PaySms(FakeClient):
    async def send_code_request(self, phone, force_sms=False):
        raise AttributeError("'SentCodePaymentRequired' object has no attribute 'type'")
r, out = capture(PaySms(), "1", "3")
assert isinstance(r, bot.WebClient) and "только за деньги" in out and "официальном приложении" in out, out
print("l10 ok: SMS платные — объясняет, что открыть аккаунт в приложении Telegram")

# 11) Telegram требует привязать почту для входа
class NeedEmail(FakeClient):
    async def send_code_request(self, phone, force_sms=False):
        return types.SimpleNamespace(type=named("SentCodeTypeSetUpEmailRequired"))
r, out = capture(NeedEmail(), "1", "3")
assert isinstance(r, bot.WebClient) and "Почта для входа" in out, out
print("l11 ok: нужна почта для входа — говорит, где её привязать")

# 12) код ушёл на почту; подсказка про следующий способ и время ожидания
class ByEmail(FakeClient):
    async def send_code_request(self, phone, force_sms=False):
        self.sent.append(1)
        return types.SimpleNamespace(type=named("SentCodeTypeEmailCode", email_pattern="a***@mail.ru"),
                                     next_type=named("CodeTypeSms"), timeout=60)
c = ByEmail()
r, out = capture(c, "1", "12345")
assert r is c and c.authorized and "a***@mail.ru" in out and "через 60 сек" in out and "по SMS" in out, out
print("l12 ok: код на почту — показывает адрес и когда можно просить SMS")

# 13) повторная отправка не удалась — бот не бросает вход, код, что уже пришёл, принимается
class NoResend(FakeClient):
    async def send_code_request(self, phone, force_sms=False):
        if self.sent:
            raise type("SendCodeUnavailableError", (Exception,), {})("no other way")
        self.sent.append(0)
        return types.SimpleNamespace(type=SentApp())
c = NoResend()
r, out = capture(c, "1", "r", "12345")
assert r is c and c.authorized and "не даёт другого способа" in out, out
print("l13 ok: r не сработал — объясняет и ждёт код дальше")

# 14) старый telethon: Telegram его не пускает -> «обнови telethon»
class OldLib(FakeClient):
    async def send_code_request(self, phone, force_sms=False):
        raise type("UpdateAppToLoginError", (Exception,), {})("update app")
r, out = capture(OldLib(), "1", "3")
assert "pip install -U telethon" in out, out
tele.__version__ = "1.24.0"
ok, txt = bot.telethon_version_note()
assert not ok and "pip install -U telethon" in txt
tele.__version__ = "1.45.0"
assert bot.telethon_version_note()[0]
print("l14 ok: устаревший telethon — подсказывает обновить")

# 15) ссылка: честно пишет, что на этом же телефоне её не открыть
c = FakeClient()
r, out = capture(c, "2")
assert r is c and "ДРУГИМ устройством" in out, out
print("l15 ok: вход по ссылке — объясняет про второе устройство")
print("ВСЕ ТЕСТЫ ВХОДА ПРОШЛИ")
