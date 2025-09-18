"""Gestione dei comandi relativi alle ricompense."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from aiogram import Router, types
from aiogram.filters import Command, CommandObject

from reward_service import RewardService


@dataclass(slots=True)
class RewardHandlers:
    """Router dedicato a classifiche e progressi premi."""

    reward_service: RewardService
    logger: Any

    def __post_init__(self) -> None:
        self.router = Router()
        self.router.message.register(self.leaderboard_command, Command("classifica"))
        self.router.message.register(self.progress_command, Command("progressi"))

    async def leaderboard_command(
        self, message: types.Message, command: CommandObject
    ) -> None:
        period_arg: Optional[str] = None
        if command.args:
            period_arg = command.args.strip().split()[0]

        period = self.reward_service.normalize_period(period_arg)
        leaderboard = await self.reward_service.get_leaderboard(period=period, limit=10)
        if not leaderboard:
            await message.answer(
                "Nessun dato disponibile per la classifica richiesta."
            )
            return

        text = self.reward_service.build_leaderboard_message(
            leaderboard,
            period=period,
            include_header=True,
        )
        await message.answer(text, parse_mode="Markdown")

    async def progress_command(
        self, message: types.Message, command: CommandObject
    ) -> None:
        username: Optional[str] = None
        period_arg: Optional[str] = None

        if command.args:
            parts = command.args.split()
            if parts:
                username = parts[0]
            if len(parts) > 1:
                period_arg = parts[1]

        if not username and message.from_user:
            username = message.from_user.username

        if not username:
            await message.answer(
                "Specifica uno username: /progressi <username> [periodo]"
            )
            return

        username = username.strip()
        if not username:
            await message.answer(
                "Specifica uno username: /progressi <username> [periodo]"
            )
            return

        period = self.reward_service.normalize_period(period_arg)
        progress = await self.reward_service.get_user_progress(username, period=period)
        if not progress:
            await message.answer(f"Nessun dato trovato per {username}.")
            return

        text = self.reward_service.build_progress_message(progress, period=period)
        await message.answer(text, parse_mode="Markdown")
