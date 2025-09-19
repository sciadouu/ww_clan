"""Servizio dedicato alla gestione della lista membri del clan."""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional

import aiohttp
from aiogram import Bot, types
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from services.db_manager import MongoManager


@dataclass
class MemberListService:
    """Coordina la generazione e l'aggiornamento della lista membri."""

    bot: Bot
    db_manager: MongoManager
    wolvesville_api_key: str
    clan_id: str
    logger: logging.Logger

    _MESSAGE_DELAY_SECONDS = 1.05
    _RETRY_AFTER_PADDING = 0.1

    def __post_init__(self) -> None:
        self._lock = asyncio.Lock()

    async def send_member_list(self, message: types.Message) -> List[types.Message]:
        """Invia la lista dei membri nella chat del messaggio fornito."""

        chat_id = message.chat.id
        thread_id = getattr(message, "message_thread_id", None)

        async with self._lock:
            messages_payload = await self._build_member_messages()
            await self._remove_previous_message(chat_id, thread_id)
            sent_messages: List[types.Message] = []
            for index, text in enumerate(messages_payload):
                send_operation = lambda text=text: message.answer(text, parse_mode="HTML")
                sent = await self._send_with_retry(send_operation)
                sent_messages.append(sent)
                if index + 1 < len(messages_payload):
                    await asyncio.sleep(self._MESSAGE_DELAY_SECONDS)

            message_ids = [sent.message_id for sent in sent_messages]
            if message_ids:
                await self.db_manager.upsert_member_list_message(
                    chat_id,
                    message_ids,
                    message_thread_id=thread_id,
                )
            else:
                await self.db_manager.delete_member_list_message(chat_id, thread_id)

            return sent_messages

    async def refresh_member_lists(self) -> None:
        """Rigenera la lista per tutte le chat in cui è stata pubblicata."""

        async with self._lock:
            stored_messages = await self.db_manager.list_member_list_messages()
            if not stored_messages:
                return

            messages_payload = await self._build_member_messages()
            for entry in stored_messages:
                chat_id = entry.get("chat_id")
                thread_id = entry.get("message_thread_id")
                stored_message_ids: List[int] = []

                raw_ids = entry.get("message_ids")
                if isinstance(raw_ids, (list, tuple)):
                    for value in raw_ids:
                        if isinstance(value, int):
                            stored_message_ids.append(value)

                legacy_message_id = entry.get("message_id")
                if isinstance(legacy_message_id, int) and legacy_message_id not in stored_message_ids:
                    stored_message_ids.insert(0, legacy_message_id)

                if stored_message_ids:
                    stored_message_ids = list(dict.fromkeys(stored_message_ids))

                if chat_id is None or not stored_message_ids:
                    continue

                should_remove_entry = False
                remove_record = False
                for stored_id in stored_message_ids:
                    try:
                        await self.bot.delete_message(chat_id, stored_id)
                    except TelegramForbiddenError:
                        should_remove_entry = True
                        self.logger.info(
                            "Impossibile aggiornare la lista membri in %s: accesso negato", chat_id
                        )
                        break
                    except TelegramBadRequest as exc:
                        if "message to delete not found" in str(exc):
                            remove_record = True
                        else:
                            self.logger.debug(
                                "Messaggio lista membri non eliminato (%s): %s",
                                chat_id,
                                exc,
                            )
                    except Exception as exc:  # pragma: no cover - log difensivo
                        self.logger.warning(
                            "Errore durante l'eliminazione della lista membri in %s: %s",
                            chat_id,
                            exc,
                        )

                if remove_record:
                    await self.db_manager.delete_member_list_message(chat_id, thread_id)

                if should_remove_entry:
                    await self.db_manager.delete_member_list_message(chat_id, thread_id)
                    continue

                sent_messages: List[types.Message] = []
                for index, text in enumerate(messages_payload):
                    try:
                        send_operation = lambda text=text: self.bot.send_message(
                            chat_id,
                            text,
                            parse_mode="HTML",
                            message_thread_id=thread_id,
                        )
                        sent = await self._send_with_retry(send_operation)
                    except TelegramForbiddenError:
                        await self.db_manager.delete_member_list_message(chat_id, thread_id)
                        self.logger.info(
                            "Impossibile inviare la nuova lista membri in %s: accesso negato",
                            chat_id,
                        )
                        should_remove_entry = True
                        break
                    except Exception as exc:  # pragma: no cover - log difensivo
                        await self.db_manager.delete_member_list_message(chat_id, thread_id)
                        self.logger.warning(
                            "Errore durante l'invio della lista membri aggiornata in %s: %s",
                            chat_id,
                            exc,
                        )
                        should_remove_entry = True
                        break

                    sent_messages.append(sent)
                    if index + 1 < len(messages_payload):
                        await asyncio.sleep(self._MESSAGE_DELAY_SECONDS)

                if should_remove_entry:
                    for sent in sent_messages:
                        try:
                            await self.bot.delete_message(chat_id, sent.message_id)
                        except Exception:  # pragma: no cover - clean-up best effort
                            pass
                    continue

                message_ids = [sent.message_id for sent in sent_messages]
                if not message_ids:
                    await self.db_manager.delete_member_list_message(chat_id, thread_id)
                    continue

                await self.db_manager.upsert_member_list_message(
                    chat_id,
                    message_ids,
                    message_thread_id=thread_id,
                )
                await asyncio.sleep(0.2)

    async def _build_member_messages(self) -> List[str]:
        entries = await self._collect_member_entries()
        if not entries:
            return [
                "📋 <b>Lista membri del clan</b>\n"
                "Nessun membro è stato trovato al momento."
            ]

        messages: List[str] = []
        for index, entry in enumerate(entries, start=1):
            prefix = "📋 <b>Lista membri del clan</b>\n" if index == 1 else ""
            contact = entry.get("telegram_contact") or entry.get("telegram_tag") or "—"
            game_name = entry.get("game_name", "—")
            telegram_name = entry.get("telegram_name", "—")
            line = f"{index}. Game Name: {game_name} | Username: {telegram_name} | tag telegram: {contact}"
            messages.append(f"{prefix}{line}")
        return messages

    async def _collect_member_entries(self) -> List[Dict[str, str]]:
        members = await self._fetch_clan_members()
        usernames = [
            member.get("username")
            for member in members
            if isinstance(member, dict) and member.get("username")
        ]

        profiles_map = await self.db_manager.get_profiles_by_game_usernames(usernames)

        entries: List[Dict[str, str]] = []
        for username in usernames:
            normalized = username.strip().lower()
            profile = profiles_map.get(normalized)

            game_name = self._escape(username)
            if profile:
                telegram_name_raw = profile.get("full_name") or profile.get("telegram_username")
                telegram_name = self._escape(telegram_name_raw) if telegram_name_raw else "—"
                telegram_username = profile.get("telegram_username")
                telegram_id = profile.get("telegram_id")
                telegram_contact = self._format_contact(
                    telegram_username,
                    telegram_name,
                    telegram_id,
                )
                telegram_tag = self._format_tag(telegram_username)
            else:
                telegram_name = "non collegato"
                telegram_contact = "—"
                telegram_tag = "—"

            entries.append(
                {
                    "game_name": game_name,
                    "telegram_name": telegram_name,
                    "telegram_tag": telegram_tag,
                    "telegram_contact": telegram_contact,
                }
            )

        return entries

    async def _fetch_clan_members(self) -> List[Dict[str, str]]:
        url = f"https://api.wolvesville.com/clans/{self.clan_id}/members"
        headers = {
            "Authorization": f"Bot {self.wolvesville_api_key}",
            "Accept": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        raise RuntimeError(
                            f"Status {response.status} durante il recupero dei membri del clan"
                        )
                    payload = await response.json()
        except Exception as exc:
            self.logger.error("Errore durante il recupero dei membri del clan: %s", exc)
            raise

        if isinstance(payload, list):
            return payload  # type: ignore[return-value]
        if isinstance(payload, dict):
            # Alcune risposte potrebbero essere contenute in una chiave specifica
            members = payload.get("members")
            if isinstance(members, list):
                return members  # type: ignore[return-value]
        return []

    async def _remove_previous_message(
        self, chat_id: int, message_thread_id: Optional[int]
    ) -> None:
        existing = await self.db_manager.get_member_list_message(chat_id, message_thread_id)
        if not existing:
            return

        message_ids: List[int] = []
        raw_ids = existing.get("message_ids")
        if isinstance(raw_ids, (list, tuple)):
            for value in raw_ids:
                if isinstance(value, int):
                    message_ids.append(value)

        legacy_message_id = existing.get("message_id")
        if isinstance(legacy_message_id, int) and legacy_message_id not in message_ids:
            message_ids.insert(0, legacy_message_id)

        if message_ids:
            message_ids = list(dict.fromkeys(message_ids))

        if not message_ids:
            await self.db_manager.delete_member_list_message(chat_id, message_thread_id)
            return

        remove_record = False
        for stored_id in message_ids:
            try:
                await self.bot.delete_message(chat_id, stored_id)
            except TelegramForbiddenError:
                await self.db_manager.delete_member_list_message(chat_id, message_thread_id)
                return
            except TelegramBadRequest as exc:
                if "message to delete not found" in str(exc):
                    remove_record = True
                else:
                    self.logger.debug(
                        "Impossibile rimuovere il messaggio %s della lista membri in %s: %s",
                        stored_id,
                        chat_id,
                        exc,
                    )
            except Exception as exc:  # pragma: no cover - log difensivo
                self.logger.debug(
                    "Impossibile rimuovere il vecchio messaggio della lista membri in %s: %s",
                    chat_id,
                    exc,
                )

        if remove_record:
            await self.db_manager.delete_member_list_message(chat_id, message_thread_id)

    async def _send_with_retry(
        self, operation: Callable[[], Awaitable[types.Message]]
    ) -> types.Message:
        while True:
            try:
                return await operation()
            except TelegramRetryAfter as exc:
                delay = float(exc.retry_after) + self._RETRY_AFTER_PADDING
                delay = max(delay, self._MESSAGE_DELAY_SECONDS)
                self.logger.info(
                    "Limite di invio Telegram raggiunto, attesa di %.2f secondi",
                    delay,
                )
                await asyncio.sleep(delay)

    @staticmethod
    def _escape(value: str) -> str:
        return html.escape(value, quote=False)

    @staticmethod
    def _format_tag(username: Optional[str]) -> str:
        if not username:
            return "—"
        clean = username.strip()
        if not clean:
            return "—"
        if clean.startswith("@"):
            clean = clean[1:]
        escaped = MemberListService._escape(clean)
        return f"@{escaped}" if escaped else "—"

    @staticmethod
    def _format_contact(
        username: Optional[str], display_name: str, telegram_id: Optional[int]
    ) -> str:
        tag = MemberListService._format_tag(username)
        if tag != "—":
            return tag
        if telegram_id and display_name and display_name != "—":
            return f'<a href="tg://user?id={telegram_id}">{display_name}</a>'
        if telegram_id:
            return f'<a href="tg://user?id={telegram_id}">Profilo Telegram</a>'
        return "—"
