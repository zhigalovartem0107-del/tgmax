"""Проверки запуска Telegram и целостности скачанных документов."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon.crypto import AuthKey
from telethon.sessions import StringSession

import tg_to_max as bot


class StartupDone(BaseException):
    """Останавливает main после выбора клиента, до циклов Telegram и MAX."""


class TelegramReliability(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        previous = Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, previous)

    async def start(self, connect, session_string="", file_ready=False):
        with (
            patch.dict(os.environ, SESSION_STRING=session_string),
            patch.object(bot, "tg_client", side_effect=connect),
            patch.object(bot, "TG_READY", file_ready),
            patch.object(bot, "NO_TG", False),
            patch.object(bot, "CLIENT_MODE", False),
            patch.object(bot, "LOGIN", False),
            patch.object(bot, "TG_CLIENT", None),
            patch.object(bot, "MAIN_LOOP", None),
            patch.object(bot, "owner_alert_err"),
            patch.object(bot.POOL, "start", AsyncMock(side_effect=StartupDone)),
        ):
            with self.assertRaises(StartupDone):
                await bot.main()
            return bot.TG_CLIENT.cur

    @staticmethod
    def fake_client(authorized=True):
        return SimpleNamespace(
            connect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=authorized),
            disconnect=AsyncMock(),
        )

    @staticmethod
    def exported_session():
        session = StringSession()
        session.set_dc(2, "192.0.2.1", 443)
        session.auth_key = AuthKey(data=b"x" * 256)
        return session.save()

    async def test_start_uses_string_session_before_file(self):
        local = Path("session.session")
        local.write_bytes(b"local session")
        client = self.fake_client()
        calls = []

        def connect(value, *_):
            calls.append(value)
            return client

        self.assertIs(await self.start(connect, self.exported_session(), file_ready=True), client)
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0], StringSession)
        self.assertEqual(local.read_bytes(), b"local session")

    async def test_start_uses_string_session_without_file(self):
        client = self.fake_client()
        self.assertIs(await self.start(lambda *_: client, self.exported_session()), client)
        self.assertFalse(Path("session.session").exists())

    async def test_web_flag_disables_account_use(self):
        Path(bot.WEB_FLAG).touch()
        account = self.fake_client()
        self.assertIsInstance(await self.start(lambda *_: account, self.exported_session(), True), bot.WebClient)
        account.connect.assert_not_awaited()

    async def test_invalid_string_uses_local_session(self):
        Path("session.session").write_bytes(b"local session")
        client = self.fake_client()
        calls = []

        def connect(value, *_):
            calls.append(value)
            return client

        self.assertIs(await self.start(connect, "invalid", file_ready=True), client)
        self.assertEqual(calls, ["session"])

    async def test_network_failure_preserves_local_session(self):
        local = Path("session.session")
        local.write_bytes(b"local session")
        client = self.fake_client()
        client.connect.side_effect = ConnectionError("offline")

        self.assertIsInstance(await self.start(lambda *_: client, file_ready=True), bot.WebClient)
        self.assertEqual(local.read_bytes(), b"local session")
        client.disconnect.assert_awaited_once()

    async def test_logout_from_string_session_keeps_disk_file(self):
        local = Path("session.session")
        local.write_bytes(b"local session")
        client = self.fake_client()
        client.session = StringSession(self.exported_session())
        async def log_out():
            client.session = None  # Telethon очищает session после выхода
        client.log_out = AsyncMock(side_effect=log_out)
        box = bot.ClientBox(client)
        with patch.object(bot, "TG_CLIENT", box), patch.dict(bot.SHARED, on=False):
            await bot.tg_logout()
        client.log_out.assert_awaited_once()
        self.assertIsInstance(box.cur, bot.WebClient)
        self.assertTrue(Path(bot.WEB_FLAG).exists())
        self.assertEqual(local.read_bytes(), b"local session")

    async def test_truncated_document_retries_and_returns_complete_copy(self):
        data = b"complete mcaddon"
        msg = SimpleNamespace(
            document=SimpleNamespace(size=len(data)),
            download_media=AsyncMock(),
        )
        payloads = iter([data[:4], data])

        async def download_media(file):
            Path(file).write_bytes(next(payloads))
            return file

        msg.download_media.side_effect = download_media
        client = SimpleNamespace(get_messages=AsyncMock(return_value=msg))
        doc = {"id": 11, "name": "sample.mcaddon", "size": len(data)}
        with patch.object(bot, "VIDEO_RETRY_PAUSES", [0]):
            path, error = await bot.download_doc(client, "games", doc)

        self.assertIsNone(error)
        self.assertEqual(Path(path).read_bytes(), data)
        self.assertEqual(msg.download_media.await_count, 2)


if __name__ == "__main__":
    unittest.main()
