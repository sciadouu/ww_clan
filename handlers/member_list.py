"""Handlers dedicati alla pubblicazione della lista membri del clan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiogram import Router, types
from aiogram.filters import Command

from services.member_list_service import MemberListService


@dataclass
class MemberListHandlers:
    member_list_service: MemberListService
    logger: Any

    def __post_init__(self) -> None:
        self.router = Router()
        self.router.message.register(self.list_members_command, Command("membri"))

    async def list_members_command(self, message: types.Message) -> None:
        try:
            await self.member_list_service.send_member_list(message)
        except Exception as exc:  # pragma: no cover - log diagnostico
            self.logger.error("Errore durante l'invio della lista membri: %s", exc)
            await message.answer(
                "❌ Impossibile recuperare la lista dei membri al momento. Riprova più tardi."
            )
            return

        try:
            if message.chat.type != "private":
                bot_member = await message.chat.get_member(message.bot.id)
                if bot_member.can_delete_messages:
                    await message.delete()
            else:
                await message.delete()
        except Exception as exc:
            self.logger.debug("Impossibile eliminare il messaggio di comando /membri: %s", exc)
