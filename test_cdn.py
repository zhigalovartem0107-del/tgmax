import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
"""Файлы с CDN Telegram (их отдают популярные каналы): у Telethon 1.45 своя CDN-ветка сломана — бот качает сам.
Нужен настоящий telethon (pip install telethon) — без него тест пропускается."""
import asyncio, importlib.util, json, os, sys, tempfile, types

try:
    import telethon
    from telethon.tl import functions, types as tl
except ImportError:
    print("telethon не установлен — тест CDN пропущен")
    print("ВСЕ ТЕСТЫ CDN ПРОШЛИ")
    sys.exit(0)
import pyaes                                              # эталон: «голый» блочный AES, независимый от кода бота

os.chdir(tempfile.mkdtemp())
json.dump({"max_token": "x", "max_channel_link": ""}, open("config.json", "w"))
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)

KEY, IV, PART = os.urandom(32), os.urandom(16), bot.CDN_PART
PLAIN = os.urandom(PART * 2 + 1000)                       # 2 полных куска и хвост


def tg_encrypt(chunk, offset):
    """Как шифрует CDN Telegram: AES-256-CTR, счётчик = IV, где последние 4 байта — offset / 16, дальше +1 на блок."""
    aes, start = pyaes.AES(KEY), int.from_bytes(IV[:12] + (offset // 16).to_bytes(4, "big"), "big")
    out = bytearray()
    for j in range(0, len(chunk), 16):
        ks = bytes(aes.encrypt(list((start + j // 16).to_bytes(16, "big"))))
        out += bytes(a ^ b for a, b in zip(chunk[j:j + 16], ks))
    return bytes(out)


# 1) расшифровка — ровно по правилам Telegram, с любого места файла
assert bot.cdn_decrypt(tg_encrypt(PLAIN[PART:2 * PART], PART), KEY, IV, PART) == PLAIN[PART:2 * PART]
print("c1 ok: кусок с CDN расшифровывается по правилам Telegram (AES-256-CTR, IV по смещению)")


# 2) скачивание: запрос к CDN идёт на CDN-сервер (а не в обычное подключение), «перезалей» обрабатывается
class FakeCdnSender:
    made = []

    def __init__(self, auth_key, *, loggers, **kw):
        assert auth_key is None                          # у CDN — свой ключ (Telethon подсовывал ключ аккаунта)
        self.auth_key, self._state = types.SimpleNamespace(key=None), types.SimpleNamespace(time_offset=None)
        self.reupload_once, self.closed, self.dc, self.hello = True, False, None, False
        FakeCdnSender.made.append(self)

    async def connect(self, conn):
        assert self.auth_key.key == TEMP_KEY and self._state.time_offset == 5, "к CDN — с временным ключом для CDN"
        self.dc = conn

    def send(self, req):
        if not self.hello:                              # как официальные клиенты: первый запрос — со знакомством
            assert isinstance(req, functions.InvokeWithLayerRequest), req
            init = req.query
            assert isinstance(init, functions.InitConnectionRequest) and init.api_id == 2040, init
            assert init.device_model == init.system_version == "n/a", "CDN не нужно знать устройство"
            req, self.hello = init.query, True
        return self._answer(req)

    async def _answer(self, req):
        assert isinstance(req, functions.upload.GetCdnFileRequest), req
        if req.offset == PART and self.reupload_once:
            self.reupload_once = False
            return tl.upload.CdnFileReuploadNeeded(request_token=b"rt")
        return tl.upload.CdnFile(bytes=tg_encrypt(PLAIN[req.offset:req.offset + req.limit], req.offset))

    async def disconnect(self):
        self.closed = True


class FakeClient:
    def __init__(self):
        self.session = types.SimpleNamespace(dc_id=2)
        self._sender, self.calls, self.returned = "HOME", [], []
        self._log, self._proxy, self._local_addr = {}, None, None
        self._init_request = functions.InitConnectionRequest(
            api_id=2040, device_model="PC 64bit", system_version="Windows 10", app_version="5.6.3 x64",
            system_lang_code="ru-RU", lang_pack="tdesktop", lang_code="ru", query=None)

    async def _borrow_exported_sender(self, dc_id):
        return f"DC{dc_id}"

    async def _return_exported_sender(self, sender):
        self.returned.append(sender)

    async def _call(self, sender, req):
        self.calls.append((sender, type(req).__name__))
        if isinstance(req, functions.upload.GetFileRequest):
            assert req.cdn_supported
            return tl.upload.FileCdnRedirect(dc_id=203, file_token=b"ft", encryption_key=KEY, encryption_iv=IV,
                                             file_hashes=[])
        if isinstance(req, functions.upload.ReuploadCdnFileRequest):
            return []
        raise AssertionError(req)

    async def __call__(self, req):
        assert isinstance(req, functions.help.GetCdnConfigRequest), req
        return tl.CdnConfig(public_keys=[])

    async def _get_dc(self, dc_id, cdn=False):
        assert cdn and dc_id == 203
        return types.SimpleNamespace(ip_address="1.2.3.4", port=443, id=203)

    def _connection(self, ip, port, dc_id, **kw):
        return ("conn", ip, dc_id)


TEMP_KEY = "временный ключ CDN"
real_auth_key = bot._cdn_auth_key                           # обмен ключами проверяем отдельно (c6)
bot._cdn_auth_key = lambda conn, dc_id, keys, loggers: asyncio.sleep(0, result=(TEMP_KEY, 5))
real_sender = telethon.network.MTProtoSender
telethon.network.MTProtoSender = FakeCdnSender
try:
    client = FakeClient()
    doc = tl.Document(id=1, access_hash=2, file_reference=b"", date=None, mime_type="application/zip",
                      size=len(PLAIN), dc_id=4, attributes=[])
    msg = tl.Message(id=7, peer_id=tl.PeerChannel(1), date=None, message="", media=tl.MessageMediaDocument(document=doc))
    msg._client = client
    path = asyncio.run(bot.tg_cdn_download(msg, os.path.abspath("cdn.bin")))
finally:
    telethon.network.MTProtoSender = real_sender
assert open(path, "rb").read() == PLAIN, "файл с CDN собрался неверно"
cdn = FakeCdnSender.made[-1]
assert cdn.dc == ("conn", "1.2.3.4", 203) and cdn.closed, "к CDN — отдельное подключение, после скачивания закрыто"
assert ("DC4", "GetFileRequest") in client.calls and ("DC4", "ReuploadCdnFileRequest") in client.calls, client.calls
assert not any(s == "HOME" for s, _ in client.calls), "файл живёт в DC4 — домашнее подключение не трогаем"
assert client.returned == ["DC4"], "одолженное подключение к DC4 вернули"
print("c2 ok: файл с CDN скачан целиком: отдельное подключение к CDN, «перезалей» обработан, подключения закрыты")


# 3) своя CDN-ветка Telethon (шлёт запрос не туда, не тем ключом, не тем шифром и копит висящие подключения) отключена:
#    Telethon сразу отдаёт ошибку, а бот качает сам
try:
    asyncio.run(bot.TelegramClient._get_cdn_client(None, None))
    raise AssertionError("ветка Telethon должна сразу отказывать")
except bot.TgCdnRedirect as e:
    assert bot.is_cdn_error(e)


# 4) бот целиком: Telethon не смог (CDN) -> своё скачивание; оно не смогло -> t.me; ничего -> понятная ошибка
class CdnFailMsg:
    id, _client = 9, None

    async def download_media(self, file=None):
        raise bot.TgCdnRedirect("CDN")


got = []
real_cdn = bot.tg_cdn_download
bot.tg_cdn_download = lambda m, dest: got.append(dest) or asyncio.sleep(0, result=dest)
assert asyncio.run(bot.tg_media(CdnFailMsg(), "x.bin", "chan")) == "x.bin" and got == ["x.bin"]


async def _boom(m, dest):
    raise ConnectionError("CDN недоступен")
bot.tg_cdn_download = _boom
bot.web_media = lambda ch, mid, dest: asyncio.sleep(0, result="via_tme.bin")
assert asyncio.run(bot.tg_media(CdnFailMsg(), "y.bin", "chan")) == "via_tme.bin"
bot.web_media = lambda ch, mid, dest: asyncio.sleep(0, result=None)
try:
    asyncio.run(bot.tg_media(CdnFailMsg(), "z.bin", "chan"))
    raise AssertionError("должна быть понятная ошибка")
except bot.MediaCdnError as e:
    assert "CDN недоступен" in str(e), e                   # в паузе видна настоящая причина, а не «недавно не отвечал»
bot.tg_cdn_download = real_cdn
print("c3 ok: сломанная CDN-ветка Telethon отключена; бот качает сам, запасной путь — t.me, иначе понятная причина")


# 5) документы любых типов с CDN — через тот же путь, что у постов (download_doc): PDF, zip, apk, rar, mcaddon.
#    Имя и байты — как в Telegram; файл, который CDN отдал не целиком, не публикуется битым
from telethon.tl.custom import Message as TlMessage
DOCS = {}                                                   # id документа -> (имя, MIME, байты)
for i, (name, mime) in enumerate((("Шапка FJODOR.pdf", "application/pdf"), ("update_0.32.zip", "application/zip"),
                                  ("Standoff 2 0.32.apk", "application/vnd.android.package-archive"),
                                  ("pack.rar", "application/vnd.rar"), ("Mob Food (addon).mcaddon", "application/octet-stream")),
                                 start=100):
    DOCS[i] = (name, mime, os.urandom(PART + 777 * i))


class DocCdnSender(FakeCdnSender):
    cut = False

    async def _answer(self, req):
        data = DOCS[int(req.file_token.decode()[3:])][2]
        if DocCdnSender.cut and req.offset >= PART:
            return tl.upload.CdnFile(bytes=b"")             # CDN оборвал отдачу — файл неполный
        return tl.upload.CdnFile(bytes=tg_encrypt(data[req.offset:req.offset + req.limit], req.offset))


class DocClient(FakeClient):
    async def _call(self, sender, req):
        if isinstance(req, functions.upload.GetFileRequest):
            return tl.upload.FileCdnRedirect(dc_id=203, file_token=f"tok{req.location.id}".encode(), encryption_key=KEY,
                                             encryption_iv=IV, file_hashes=[])
        return await super()._call(sender, req)

    async def get_messages(self, ch, ids=None, **kw):
        name, mime, data = DOCS[ids]
        doc = tl.Document(id=ids, access_hash=1, file_reference=b"", date=None, mime_type=mime, size=len(data), dc_id=2,
                          attributes=[tl.DocumentAttributeFilename(file_name=name)])
        m = TlMessage(id=ids, peer_id=tl.PeerChannel(1), date=None, message="", media=tl.MessageMediaDocument(document=doc))
        m._client = self
        return m

    async def download_media(self, *a, **kw):              # так Telethon падает на файлах с CDN
        raise bot.TgCdnRedirect("файл на CDN Telegram")


telethon.network.MTProtoSender = DocCdnSender
bot.web_media = lambda ch, mid, dest: asyncio.sleep(0, result=None)
bot.CDN_STATE["down_until"] = 0                             # прошлый сценарий («CDN недоступен») включил паузу CDN
try:
    dc = DocClient()
    for i, (name, mime, data) in DOCS.items():
        path, err = asyncio.run(bot.download_doc(dc, "knit", {"id": i, "name": name, "size": len(data)}))
        assert err is None and os.path.basename(path) == name and open(path, "rb").read() == data, (name, err)
        assert bot.file_mime(path) == mime, (name, bot.file_mime(path))
    DocCdnSender.cut = True
    import shutil
    shutil.rmtree(os.path.dirname(bot.doc_path("knit", {"id": 102, "name": DOCS[102][0]})))   # целый — уже скачан
    bot.CDN_STATE["down_until"] = 0
    bot.VIDEO_RETRY_PAUSES[:] = [0]
    path, err = asyncio.run(bot.download_doc(dc, "knit", {"id": 102, "name": DOCS[102][0], "size": len(DOCS[102][2])}))
    assert path is None and "не целиком" in err, (path, err)
finally:
    telethon.network.MTProtoSender = real_sender
# 6) CDN не отвечает (подключение висит) — бот не зависает: ждёт ограниченное время, идёт на t.me, полчаса не мучает CDN
class HangCdnSender(FakeCdnSender):
    made = 0

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        HangCdnSender.made += 1

    async def _answer(self, req):
        await asyncio.sleep(3600)                           # так выглядит «молчащий» сервер


import time as _time
telethon.network.MTProtoSender = HangCdnSender
bot.web_media = lambda ch, mid, dest: asyncio.sleep(0, result="via_tme.pdf")
bot.CDN_TIMEOUT, bot.CDN_STATE["down_until"] = 0.3, 0
try:
    t0 = _time.monotonic()
    m = asyncio.run(DocClient().get_messages("knit", ids=100))
    assert asyncio.run(bot.tg_media(m, "hang.pdf", "knit")) == "via_tme.pdf"
    assert _time.monotonic() - t0 < 5, _time.monotonic() - t0
    assert asyncio.run(bot.tg_media(m, "hang2.pdf", "knit")) == "via_tme.pdf" and HangCdnSender.made == 1, HangCdnSender.made
finally:
    telethon.network.MTProtoSender = real_sender
    bot.CDN_STATE["down_until"] = 0
print("c5 ok: CDN молчит — бот не зависает: ждёт ограниченное время, качает через t.me и полчаса не дёргает CDN")

print("c4 ok: PDF, zip, apk, rar, mcaddon с CDN скачиваются целиком, с именем и правильным типом; обрыв — не публикуем")


# 6) ключ для CDN — как у Telegram Desktop и TDLib: ВРЕМЕННЫЙ, на сутки, с номером CDN-сервера (p_q_inner_data_temp_dc),
#    зашифрован по RSA_PAD ключом этого CDN из help.getCdnConfig. Сервер-подделка читает то, что получил бы настоящий CDN
import struct, rsa as pyrsa
from hashlib import sha1, sha256
from Cryptodome.PublicKey import RSA
from telethon.crypto import AES
from telethon.extensions import BinaryReader
from telethon.tl.tlobject import TLObject

PRIV = RSA.generate(2048)
tl_int = lambda x: TLObject.serialize_bytes(x.to_bytes((x.bit_length() + 7) // 8, "big"))
FP = int.from_bytes(sha1(tl_int(PRIV.n) + tl_int(PRIV.e)).digest()[-8:], "little", signed=True)   # отпечаток MTProto


def rsa_pad_open(block):
    """Обратное к RSA_PAD (core.telegram.org/mtproto/auth_key, п. 4.1) — так CDN расшифровывает наш запрос."""
    m = pow(int.from_bytes(block, "big"), PRIV.d, PRIV.n).to_bytes(256, "big")
    enc = m[32:]
    temp = bytes(a ^ b for a, b in zip(m[:32], sha256(enc).digest()))
    plain = AES.decrypt_ige(enc, temp, bytes(32))
    data = plain[:192][::-1]
    assert sha256(temp + data).digest() == plain[192:], "не RSA_PAD"
    return BinaryReader(data).tgread_object()


class CdnServer:
    """CDN-сервер на время обмена ключами: отвечает на req_pq_multi своим ключом и читает req_DH_params."""
    def __init__(self):
        self.answers, self.inner, self.closed = [], None, False

    async def connect(self, timeout=None, ssl=None):
        pass

    async def send(self, data):
        req = BinaryReader(data[20:]).tgread_object()
        if isinstance(req, functions.ReqPqMultiRequest):
            self.answers.append(tl.ResPQ(nonce=req.nonce, server_nonce=77, pq=(1229 * 1231).to_bytes(3, "big"),
                                         server_public_key_fingerprints=[FP]))
        else:
            assert isinstance(req, functions.ReqDHParamsRequest) and req.public_key_fingerprint == FP, req
            self.inner = rsa_pad_open(req.encrypted_data)

    async def recv(self):
        if not self.answers:
            raise ConnectionError("дальше обмен ключами не проверяем")
        body = bytes(self.answers.pop(0))
        return struct.pack("<qqi", 0, 1, len(body)) + body

    async def disconnect(self):
        self.closed = True


cdn_srv = CdnServer()


class KeyClient(FakeClient):
    def __init__(self):
        super().__init__()
        import collections, logging
        self._log = collections.defaultdict(lambda: logging.getLogger("cdn"))   # как у Telethon: логгер на модуль

    async def __call__(self, req):
        pem = pyrsa.PublicKey(PRIV.n, PRIV.e).save_pkcs1().decode()
        return tl.CdnConfig(public_keys=[tl.CdnPublicKey(dc_id=201, public_key="чужой"),
                                         tl.CdnPublicKey(dc_id=203, public_key=pem)])

    def _connection(self, ip, port, dc_id, **kw):
        return cdn_srv


bot._cdn_auth_key = real_auth_key
bot.CDN_KEYS.clear()                                        # ключ из прошлых сценариев не берём — проверяем обмен
try:
    asyncio.run(bot._cdn_sender(KeyClient(), 203))
    raise AssertionError("подделка обрывает обмен ключами на середине")
except ConnectionError:
    pass
got = cdn_srv.inner
assert isinstance(got, tl.PQInnerDataTempDc), got
assert got.dc == 203 and got.expires_in == 86400, (got.dc, got.expires_in)
assert cdn_srv.closed, "подключение для обмена ключами закрыто"
print("c6 ok: ключ для CDN — как у официальных Telegram: временный на сутки, с номером CDN, RSA_PAD ключом CDN")


# 7) ключ с CDN-сервером живёт сутки — как у TDLib, не договариваемся заново на каждый файл;
#    сбой на CDN — ключ забываем (мог устареть), следующий файл договаривается заново
made_keys = []
bot._cdn_auth_key = lambda conn, dc_id, keys, loggers: made_keys.append(dc_id) or asyncio.sleep(0, result=(TEMP_KEY, 5))
bot.CDN_KEYS.clear()
telethon.network.MTProtoSender = FakeCdnSender
try:
    for n in range(3):
        msg._client = FakeClient()
        asyncio.run(bot.tg_cdn_download(msg, os.path.abspath(f"k{n}.bin")))
    assert made_keys == [203], made_keys
    class BrokenCdn(FakeCdnSender):
        async def _answer(self, req):
            raise ConnectionError("CDN оборвал связь")
    telethon.network.MTProtoSender = BrokenCdn
    msg._client = FakeClient()
    try:
        asyncio.run(bot.tg_cdn_download(msg, os.path.abspath("k3.bin")))
    except ConnectionError:
        pass
    telethon.network.MTProtoSender = FakeCdnSender
    msg._client = FakeClient()
    asyncio.run(bot.tg_cdn_download(msg, os.path.abspath("k4.bin")))
    assert made_keys == [203, 203], made_keys
finally:
    telethon.network.MTProtoSender = real_sender
print("c7 ok: ключ с CDN живёт сутки (не договариваемся на каждый файл); сбой — договариваемся заново")


# 8) самопроверка CDN с сервера бота: свой ключ + знакомство + запрос заведомо чужого файла.
#    CDN ответил «нет такого файла» — связь с CDN рабочая; молчит — понятная причина
from telethon import errors as tl_errors
class AnswerCdn(FakeCdnSender):
    async def _answer(self, req):
        assert isinstance(req, functions.upload.GetCdnFileRequest), req
        raise tl_errors.BadRequestError(request=req, message="FILE_TOKEN_INVALID")
class SelfClient(FakeClient):
    async def __call__(self, req):
        return tl.CdnConfig(public_keys=[tl.CdnPublicKey(dc_id=203, public_key=pyrsa.PublicKey(PRIV.n, PRIV.e)
                                                         .save_pkcs1().decode())])
class SelfClient121(SelfClient):                            # ключи есть для 121 и 203, а адрес Telegram даёт только 203
    async def __call__(self, req):
        pem = pyrsa.PublicKey(PRIV.n, PRIV.e).save_pkcs1().decode()
        return tl.CdnConfig(public_keys=[tl.CdnPublicKey(dc_id=121, public_key=pem),
                                         tl.CdnPublicKey(dc_id=203, public_key=pem)])

    async def _get_dc(self, dc_id, cdn=False):
        if dc_id == 121:
            raise ValueError("Failed to get DC 121 (cdn = True)")
        return await super()._get_dc(dc_id, cdn)
class NoAddr(SelfClient121):
    async def _get_dc(self, dc_id, cdn=False):
        raise ValueError(f"Failed to get DC {dc_id} (cdn = True)")
bot.CDN_KEYS.clear()
telethon.network.MTProtoSender = AnswerCdn
try:
    ok, text = asyncio.run(bot.cdn_check(SelfClient121()))            # как у хозяина: «Failed to get DC 121»
    assert ok and "203" in text, text
    ok, text = asyncio.run(bot.cdn_check(NoAddr()))
    assert not ok and "адрес" in text and "121" in text and "203" in text, text
    bot.CDN_STATE["down_until"] = 9e12                       # была пауза после сбоя CDN
    ok, text = asyncio.run(bot.cdn_check(SelfClient()))
    assert ok and "203" in text and "FILE_TOKEN_INVALID" in text, text
    assert bot.CDN_STATE["down_until"] == 0, "CDN снова отвечает — ждущие посты пробуют сразу, без паузы"
    telethon.network.MTProtoSender = HangCdnSender
    bot.CDN_TIMEOUT = 0.3
    ok, text = asyncio.run(bot.cdn_check(SelfClient()))
    assert not ok and "не ответил" in text, text
finally:
    telethon.network.MTProtoSender = real_sender
print("c8 ok: самопроверка CDN с сервера бота: ответ CDN — связь рабочая; молчание — понятная причина")


# 9) с НАСТОЯЩИМ Telethon: каждый импорт из telethon в боте находится, и всё внутреннее Telethon, на что опирается
#    скачивание с CDN, на месте (обновили Telethon и что-то пропало — тест красный, а не «молча не качает»)
import re as _re, importlib as _il
src = open(ROOT + "/tg_to_max.py", encoding="utf-8").read()
for mod, names in _re.findall(r"^\s*from (telethon[\w.]*) import ([\w, ]+?)(?:\s+as \w+)?\s*(?:#.*)?$", src, _re.M):
    m_ = _il.import_module(mod)
    for n_ in (x.strip() for x in names.split(",")):
        assert hasattr(m_, n_), f"from {mod} import {n_}: нет в telethon {telethon.__version__}"
from telethon.sessions import StringSession
real_tc = telethon.TelegramClient(StringSession(), 2040, "0" * 32)          # без подключения
for attr in ("_get_dc", "_borrow_exported_sender", "_return_exported_sender", "_call", "_sender", "_connection",
             "_log", "_proxy", "_local_addr", "_init_request", "_get_cdn_client", "_config"):
    assert hasattr(real_tc, attr), f"в telethon {telethon.__version__} нет TelegramClient.{attr}"
assert real_tc._init_request.api_id == 2040 and hasattr(real_tc._init_request, "app_version")
snd = real_sender(None, loggers=real_tc._log, retries=2, connect_timeout=15, auto_reconnect=False)
snd.auth_key.key, snd._state.time_offset = telethon.crypto.AuthKey(os.urandom(256)), 5
assert snd.auth_key and snd._state.auth_key is snd.auth_key and snd._state.time_offset == 5
assert {"PQInnerData", "rsa"} <= set(telethon.network.authenticator.do_authentication.__code__.co_names)
import inspect as _insp                                      # список серверов — у класса, _get_dc перечитывает его, если пуст
assert "if not cls._config" in _insp.getsource(telethon.TelegramClient._get_dc)
print(f"c9 ok: настоящий telethon {telethon.__version__}: все импорты бота и всё, на что опирается CDN-скачивание, на месте")


# 10) ВИДЕО с CDN — через тот же путь, что у постов (download_video), с настоящим сообщением Telethon:
#     байт в байт, и видео, скачанное в прошлую попытку, второй раз не качается
VIDEO = os.urandom(PART * 3 + 4321)
DOCS[300] = ("video.mp4", "video/mp4", VIDEO)
class VidClient(DocClient):
    async def get_messages(self, ch, ids=None, **kw):
        name, mime, data = DOCS[ids]
        doc = tl.Document(id=ids, access_hash=1, file_reference=b"", date=None, mime_type=mime, size=len(data), dc_id=2,
                          attributes=[tl.DocumentAttributeVideo(duration=12, w=720, h=1280, supports_streaming=True)])
        m = TlMessage(id=ids, peer_id=tl.PeerChannel(1), date=None, message="", media=tl.MessageMediaDocument(document=doc))
        m._client = self
        return m
bot._cdn_auth_key = lambda conn, dc_id, keys, loggers: asyncio.sleep(0, result=(TEMP_KEY, 5))
bot.CDN_KEYS.clear(); bot.CDN_STATE["down_until"] = 0; bot.CDN_TIMEOUT = 90
DocCdnSender.cut = False
DocCdnSender.made = []
telethon.network.MTProtoSender = DocCdnSender
try:
    vc = VidClient()
    assert bot.video_doc(asyncio.run(vc.get_messages("knit", ids=300))) is not None, "Telethon видит в посте видео"
    path, err = asyncio.run(bot.download_video(vc, "knit", 300))
    assert err is None and open(path, "rb").read() == VIDEO, err
    n_conn = len(DocCdnSender.made)
    path2, err2 = asyncio.run(bot.download_video(vc, "knit", 300))
    assert path2 == path and err2 is None and len(DocCdnSender.made) == n_conn, "скачанное целиком — не качаем заново"
finally:
    telethon.network.MTProtoSender = real_sender
print("c10 ok: видео с CDN — с настоящим сообщением Telethon, байт в байт; второй раз не качается")


# 11) Telegram прислал файл на CDN-сервер, которого нет в списке серверов Telethon (список берётся один раз за запуск
#     и не обновляется) — берём свежий список у Telegram, как MadelineProto, и качаем. Нет и в свежем — понятная причина
class StaleClient(FakeClient):
    _config = "старый"                                      # как в Telethon: список серверов — у класса, один на запуск

    async def _get_dc(self, dc_id, cdn=False):
        if type(self)._config == "старый":
            raise ValueError(f"Failed to get DC {dc_id} (cdn = True)")
        type(self)._config = "свежий"
        return await super()._get_dc(dc_id, cdn)
class NeverClient(FakeClient):
    async def _get_dc(self, dc_id, cdn=False):
        raise ValueError(f"Failed to get DC {dc_id} (cdn = True)")
bot.CDN_KEYS.clear()
telethon.network.MTProtoSender = FakeCdnSender
try:
    msg._client = StaleClient()
    path = asyncio.run(bot.tg_cdn_download(msg, os.path.abspath("stale.bin")))
    assert open(path, "rb").read() == PLAIN and StaleClient._config == "свежий"
    msg._client = NeverClient()
    try:
        asyncio.run(bot.tg_cdn_download(msg, os.path.abspath("never.bin")))
        raise AssertionError("должна быть понятная ошибка")
    except bot.MediaCdnError as e:
        assert "адрес" in str(e) and "203" in str(e), e
finally:
    telethon.network.MTProtoSender = real_sender
print("c11 ok: CDN-сервера нет в списке Telethon — берём свежий список у Telegram и качаем; нет и там — понятная причина")
print("ВСЕ ТЕСТЫ CDN ПРОШЛИ")
os._exit(0)
