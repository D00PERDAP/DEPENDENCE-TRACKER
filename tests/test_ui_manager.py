"""Regression tests for the one-screen UX; no Telegram token or network needed."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import ui_manager


class FakeBot:
    def __init__(self):
        self.next_id = 100
        self.messages = {}
        self.deletes = []
        self.sends = 0

    async def send_message(self, chat_id, text, reply_markup=None):
        self.next_id += 1
        self.messages[self.next_id] = ("text", text)
        self.sends += 1
        return SimpleNamespace(message_id=self.next_id)

    async def send_photo(self, chat_id, photo, caption, reply_markup=None):
        self.next_id += 1
        self.messages[self.next_id] = ("photo", caption)
        self.sends += 1
        return SimpleNamespace(message_id=self.next_id)

    async def send_voice(self, chat_id, voice, caption, reply_markup=None):
        self.next_id += 1
        self.messages[self.next_id] = ("voice", caption)
        self.sends += 1
        return SimpleNamespace(message_id=self.next_id, voice=SimpleNamespace(file_id="fakevoice"))

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        if message_id not in self.messages:
            from aiogram.exceptions import TelegramBadRequest
            from aiogram.methods import EditMessageText
            raise TelegramBadRequest(
                EditMessageText(chat_id=chat_id, message_id=message_id, text=text),
                "message to edit not found",
            )
        self.messages[message_id] = ("text", text)
        return SimpleNamespace(message_id=message_id)

    async def edit_message_media(self, chat_id, message_id, media, reply_markup=None):
        if message_id not in self.messages:
            from aiogram.exceptions import TelegramBadRequest
            from aiogram.methods import EditMessageMedia
            raise TelegramBadRequest(
                EditMessageMedia(chat_id=chat_id, message_id=message_id, media=media),
                "message to edit not found",
            )
        self.messages[message_id] = ("photo", media.caption)
        return SimpleNamespace(message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        self.messages.pop(message_id, None)
        self.deletes.append(message_id)
        return True


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.previous_db = ui_manager.DB
        ui_manager.DB = Path(self.folder.name) / "ui.sqlite3"
        ui_manager._locks.clear()
        self.bot = FakeBot()
        self.command = SimpleNamespace(
            chat=SimpleNamespace(id=101),
            from_user=SimpleNamespace(is_bot=False),
            message_id=1,
            bot=self.bot,
        )

    def tearDown(self):
        ui_manager.DB = self.previous_db
        ui_manager._locks.clear()
        self.folder.cleanup()

    async def test_rich_screen_reuses_existing_text_message_via_fallback(self):
        await ui_manager.render_text(self.command, "Главная")
        await ui_manager.render_rich(self.command, "## Мага", "Мага", None)
        self.assertEqual(self.bot.sends, 1)
        self.assertEqual(len(self.bot.messages), 1)

    async def test_rich_screen_falls_back_for_unsupported_bot(self):
        await ui_manager.render_rich(self.command, "## Мага", "Мага", None)
        self.assertEqual(self.bot.sends, 1)
        self.assertEqual(list(self.bot.messages.values()), [("text", "Мага")])

    async def test_rich_after_photo_cleans_old_message(self):
        await ui_manager.render_photo(self.command, b"photo", "Мем")
        await ui_manager.render_rich(self.command, "## Мага", "Мага", None)
        self.assertEqual(len(self.bot.messages), 1)
        self.assertEqual(self.bot.sends, 2)

    async def test_start_twice_edits_old_screen(self):
        await ui_manager.render_text(self.command, "Первая")
        await ui_manager.render_text(self.command, "Вторая")
        self.assertEqual(self.bot.sends, 1)
        self.assertEqual(len(self.bot.messages), 1)

    async def test_twenty_fast_taps_do_not_flood(self):
        await asyncio.gather(*(ui_manager.render_text(self.command, f"Меню {i}")
                               for i in range(20)))
        self.assertEqual(self.bot.sends, 1)
        self.assertEqual(len(self.bot.messages), 1)

    async def test_photo_and_text_replace_incompatible_types(self):
        await ui_manager.render_text(self.command, "Главная")
        await ui_manager.render_photo(self.command, b"fake", "Статистика")
        self.assertEqual(len(self.bot.messages), 1)
        await ui_manager.render_photo(self.command, b"fake", "Другой мем")
        self.assertEqual(len(self.bot.messages), 1)
        await ui_manager.render_text(self.command, "Помощь")
        self.assertEqual(len(self.bot.messages), 1)
        self.assertEqual(self.bot.sends, 3)

    async def test_voice_preview_is_one_screen_and_redo_replaces_it(self):
        await ui_manager.render_text(self.command, "Меню")
        preview = await ui_manager.render_voice(self.command, "file-id", "Прослушивание")
        self.assertEqual(len(self.bot.messages), 1)
        self.assertEqual(self.bot.messages[preview.message_id][0], "voice")
        self.assertEqual(self.bot.sends, 2)
        self.assertEqual(ui_manager._get(101)[0], preview.message_id)
        await ui_manager.render_text(self.command, "Переделать текст")
        self.assertEqual(len(self.bot.messages), 1)
        self.assertEqual(list(self.bot.messages.values()), [("text", "Переделать текст")])
        self.assertIn(preview.message_id, self.bot.deletes)

    async def test_generating_new_preview_does_not_leave_an_old_one(self):
        await ui_manager.render_text(self.command, "Генерируем")
        first = await ui_manager.render_voice(self.command, "file-id-1", "Первое")
        second = await ui_manager.render_voice(self.command, "file-id-2", "Второе")
        self.assertEqual(len(self.bot.messages), 1)
        self.assertNotEqual(first.message_id, second.message_id)
        self.assertEqual(self.bot.messages[second.message_id], ("voice", "Второе"))

    async def test_deleted_screen_recovers(self):
        await ui_manager.render_text(self.command, "Главная")
        self.bot.messages.clear()
        await ui_manager.render_text(self.command, "Новое меню")
        self.assertEqual(len(self.bot.messages), 1)
        self.assertEqual(self.bot.sends, 2)

    async def test_persisted_pointer_survives_manager_restart(self):
        await ui_manager.render_text(self.command, "Главная")
        self.assertIsNotNone(ui_manager._get(101))
        ui_manager._locks.clear()
        await ui_manager.render_text(self.command, "После рестарта")
        self.assertEqual(self.bot.sends, 1)

    async def test_different_users_are_isolated(self):
        another = SimpleNamespace(chat=SimpleNamespace(id=202),
                                  from_user=SimpleNamespace(is_bot=False),
                                  message_id=2, bot=self.bot)
        await ui_manager.render_text(self.command, "Первый")
        await ui_manager.render_text(another, "Второй")
        self.assertEqual(self.bot.sends, 2)
        self.assertNotEqual(ui_manager._get(101)[0], ui_manager._get(202)[0])


if __name__ == "__main__":
    unittest.main()
