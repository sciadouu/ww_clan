"""Servizio dedicato alla gestione della lista membri del clan."""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import aiohttp
from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from services.db_manager import MongoManager


@dataclass
class MemberListService:
    """Coordina la generazione e l'aggiornamento della lista membri."""

    bot: Bot
    db_manager: MongoManager
    wolvesville_api_key: str
    clan_id: str
    logger: logging.Logger

    def __post_init__(self) -> None:
        self._lock = asyncio.Lock()

    async def send_member_list(self, message: types.Message) -> types.Message:
        """Invia la lista dei membri nella chat del messaggio fornito."""

        chat_id = message.chat.id
        thread_id = getattr(message, "message_thread_id", None)

        async with self._lock:
            text = await self._build_member_list_text()
            await self._remove_previous_message(chat_id, thread_id)
            sent_message = await message.answer(text)
            await self.db_manager.upsert_member_list_message(
                chat_id,
                sent_message.message_id,
                message_thread_id=thread_id,
            )
            return sent_message

    async def refresh_member_lists(self) -> None:
        """Rigenera la lista per tutte le chat in cui è stata pubblicata."""

        async with self._lock:
            stored_messages = await self.db_manager.list_member_list_messages()
            if not stored_messages:
                return

            text = await self._build_member_list_text()
            for entry in stored_messages:
                chat_id = entry.get("chat_id")
                message_id = entry.get("message_id")
                thread_id = entry.get("message_thread_id")

                if chat_id is None or message_id is None:
                    continue

                should_remove_entry = False
                try:
                    await self.bot.delete_message(chat_id, message_id)
                except TelegramForbiddenError:
                    should_remove_entry = True
                    self.logger.info(
                        "Impossibile aggiornare la lista membri in %s: accesso negato", chat_id
                    )
                except TelegramBadRequest as exc:
                    if "message to delete not found" in str(exc):
                        should_remove_entry = False
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

                if should_remove_entry:
                    await self.db_manager.delete_member_list_message(chat_id, thread_id)
                    continue

                try:
                    sent = await self.bot.send_message(
                        chat_id,
                        text,
                        message_thread_id=thread_id,
                    )
                except TelegramForbiddenError:
                    await self.db_manager.delete_member_list_message(chat_id, thread_id)
                    self.logger.info(
                        "Impossibile inviare la nuova lista membri in %s: accesso negato",
                        chat_id,
                    )
                    continue
                except Exception as exc:  # pragma: no cover - log difensivo
                    self.logger.warning(
                        "Errore durante l'invio della lista membri aggiornata in %s: %s",
                        chat_id,
                        exc,
                    )
                    continue

                await self.db_manager.upsert_member_list_message(
                    chat_id,
                    sent.message_id,
                    message_thread_id=thread_id,
                )
                await asyncio.sleep(0.2)

    async def _build_member_list_text(self) -> str:
        entries = await self._collect_member_entries()
        if not entries:
            return (
                "📋 <b>Lista membri del clan</b>\n"
                "Nessun membro è stato trovato al momento."
            )

        lines = ["📋 <b>Lista membri del clan</b>", ""]
        for index, entry in enumerate(entries, start=1):
            line = (
                f"{index}. Nome in gioco: {entry['game_name']} "
                f"Nome telegram: {entry['telegram_name']} "
                f"tag telegram(se presente): {entry['telegram_tag']}"
            )
            lines.append(line)
        return "\n".join(lines)

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
                telegram_tag_raw = profile.get("telegram_username")
                telegram_tag = self._format_tag(telegram_tag_raw)
            else:
                telegram_name = "non collegato"
                telegram_tag = "—"

            entries.append(
                {
                    "game_name": game_name,
                    "telegram_name": telegram_name,
                    "telegram_tag": telegram_tag,
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

        message_id = existing.get("message_id")
        if not message_id:
            await self.db_manager.delete_member_list_message(chat_id, message_thread_id)
            return

        try:
            await self.bot.delete_message(chat_id, message_id)
        except TelegramForbiddenError:
            await self.db_manager.delete_member_list_message(chat_id, message_thread_id)
        except TelegramBadRequest as exc:
            if "message to delete not found" in str(exc):
                await self.db_manager.delete_member_list_message(chat_id, message_thread_id)
        except Exception as exc:  # pragma: no cover - log difensivo
            self.logger.debug(
                "Impossibile rimuovere il vecchio messaggio della lista membri in %s: %s",
                chat_id,
                exc,
            )

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
        return clean if clean.startswith("@") else f"@{clean}"
