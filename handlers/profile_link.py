"""Handlers dedicated to linking Telegram profiles with Wolvesville accounts."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote_plus

import aiohttp
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.db_manager import MongoManager
from services.identity_service import (
    IdentityService,
    format_markdown_code,
    format_telegram_username,
)
from services.notification_service import NotificationType


class LinkStates(StatesGroup):
    WAITING_GAME_USERNAME = State()
    WAITING_VERIFICATION = State()


@dataclass
class ProfileLinkHandlers:
    identity_service: IdentityService
    db_manager: MongoManager
    clan_id: str
    wolvesville_api_key: str
    schedule_admin_notification: Callable[..., Any]
    logger: Any

    def __post_init__(self) -> None:
        self.router = Router()
        self.router.message.register(self.link_profile_command, Command("collega"))
        self.router.message.register(
            self.receive_game_username, LinkStates.WAITING_GAME_USERNAME
        )
        self.router.message.register(
            self.remind_verification_step, LinkStates.WAITING_VERIFICATION
        )
        self.router.callback_query.register(
            self.cancel_linking,
            LinkStates.WAITING_VERIFICATION,
            F.data == "link_cancel",
        )
        self.router.callback_query.register(
            self.finalize_profile_link,
            LinkStates.WAITING_VERIFICATION,
            F.data == "link_verify",
        )

    async def link_profile_command(
        self, message: types.Message, state: FSMContext
    ) -> None:
        if message.chat.type != "private":
            await message.answer(
                "🔒 Per motivi di sicurezza esegui /collega in chat privata con il bot."
            )
            return

        await state.clear()
        await self.identity_service.ensure_telegram_profile_synced(message.from_user)

        profile = await self.db_manager.get_profile_by_telegram_id(message.from_user.id)
        lines = [
            "🔗 <b>Collegamento profilo Wolvesville</b>",
            "Inviami ora il tuo username di gioco esattamente come appare in Wolvesville.",
        ]
        if profile and profile.get("game_username"):
            lines.append(
                f"Attualmente risulti collegato a <b>{profile['game_username']}</b>."
            )
        lines.append(
            "Se il tuo username è cambiato ripeti questa procedura per mantenere il database allineato."
        )

        await message.answer("\n\n".join(lines))
        await state.set_state(LinkStates.WAITING_GAME_USERNAME)

    async def receive_game_username(
        self, message: types.Message, state: FSMContext
    ) -> None:
        if message.chat.type != "private":
            await message.answer(
                "⚠️ Completa il collegamento in chat privata con il bot per motivi di sicurezza."
            )
            return

        username = (message.text or "").strip()
        if not username:
            await message.answer("❌ Inserisci uno username valido.")
            return

        player_info = await self._fetch_player_by_username(username)
        if not player_info or not player_info.get("id"):
            await message.answer(
                "❌ Non ho trovato alcun giocatore con questo username. "
                "Controlla l'ortografia e riprova."
            )
            return

        canonical_username = player_info.get("username") or username
        clan_id = player_info.get("clanId")
        if clan_id != self.clan_id:
            await message.answer(
                "⚠️ Il profilo indicato non risulta appartenere al clan. "
                "Contatta un amministratore se ritieni si tratti di un errore."
            )
            return

        verification_code = self._generate_verification_code()
        await state.update_data(
            pending_username=canonical_username,
            player_id=player_info.get("id"),
            verification_code=verification_code,
        )

        instructions = (
            f"Per verificare la proprietà dell'account <b>{canonical_username}</b> inserisci il codice "
            f"<code>{verification_code}</code> nel tuo messaggio personale su Wolvesville.\n"
            "Dopo averlo aggiornato premi il pulsante qui sotto. Potrai rimuovere il codice dal profilo una volta completata la verifica."
        )

        await message.answer(
            instructions, reply_markup=self._build_verification_keyboard()
        )
        await state.set_state(LinkStates.WAITING_VERIFICATION)

    async def remind_verification_step(
        self, message: types.Message, state: FSMContext
    ) -> None:
        data = await state.get_data()
        code = data.get("verification_code", "")
        reminder = (
            "Quando hai aggiornato il tuo messaggio personale con il codice "
            f"<code>{code}</code> premi il pulsante ✅ Ho aggiornato il profilo."
        )
        await message.answer(reminder, reply_markup=self._build_verification_keyboard())

    async def cancel_linking(
        self, callback: types.CallbackQuery, state: FSMContext
    ) -> None:
        await state.clear()
        try:
            await callback.message.edit_reply_markup()
        except Exception:
            pass
        await callback.answer("Collegamento annullato")
        await callback.message.answer(
            "❎ Collegamento annullato. Potrai ripetere il comando /collega quando vorrai."
        )

    async def finalize_profile_link(
        self, callback: types.CallbackQuery, state: FSMContext
    ) -> None:
        await self.identity_service.ensure_telegram_profile_synced(callback.from_user)

        data = await state.get_data()
        username = data.get("pending_username")
        verification_code = data.get("verification_code")
        player_id = data.get("player_id")

        if not username or not verification_code or not player_id:
            await callback.answer(
                "Sessione scaduta, ripeti /collega per ricominciare.",
                show_alert=True,
            )
            await state.clear()
            return

        player_info = await self.identity_service.fetch_player_by_id(player_id)
        if not player_info:
            await callback.answer(
                "Non riesco a recuperare il profilo, riprova tra qualche secondo.",
                show_alert=True,
            )
            return

        personal_message = player_info.get("personalMessage") or ""
        if verification_code not in personal_message:
            await callback.answer(
                "Non ho trovato il codice nel tuo messaggio personale. "
                "Assicurati di averlo inserito e riprova.",
                show_alert=True,
            )
            return

        result = await self.db_manager.link_player_profile(
            callback.from_user.id,
            game_username=player_info.get("username", username),
            telegram_username=callback.from_user.username,
            full_name=" ".join(
                part
                for part in [callback.from_user.first_name, callback.from_user.last_name]
                if part
            ).strip()
            or None,
            wolvesville_id=player_info.get("id"),
            verified=True,
            verification_code=verification_code,
            verification_method="personal_message",
        )

        if result.get("conflict"):
            await callback.answer(
                "Questo profilo è già collegato a un altro utente. Contatta un admin.",
                show_alert=True,
            )
            return

        updated_profile = await self.identity_service.handle_profile_link_result(result)
        profile = updated_profile or result.get("profile") or {}
        telegram_username_display = format_telegram_username(
            profile.get("telegram_username")
        )

        summary_lines = [
            "✅ <b>Collegamento completato!</b>",
            f"🎮 Username di gioco: <b>{profile.get('game_username', username)}</b>",
            f"💬 Telegram: {telegram_username_display}",
            "Ricorda di rimuovere il codice dal tuo messaggio personale.",
        ]
        if result.get("game_username_changed") and result.get("previous_game_username"):
            summary_lines.append(
                f"🔁 Nome precedente registrato: {result['previous_game_username']}"
            )

        await state.clear()
        try:
            await callback.message.edit_text("\n".join(summary_lines))
        except Exception:
            await callback.message.answer("\n".join(summary_lines))

        await callback.answer("Profilo verificato!", show_alert=False)

        admin_lines = [
            "🔗 **Profilo Wolvesville collegato**",
            f"🎮 **Username:** {format_markdown_code(profile.get('game_username', username))}",
            f"🆔 **Wolvesville ID:** {format_markdown_code(profile.get('wolvesville_id'))}",
            f"💬 **Telegram:** {format_markdown_code(telegram_username_display)}",
            f"🆔 **Telegram ID:** {format_markdown_code(profile.get('telegram_id'))}",
        ]
        if result.get("created"):
            admin_lines.append("✨ Nuovo collegamento creato.")
        if result.get("game_username_changed") and result.get("previous_game_username"):
            admin_lines.append(
                "🔁 **Username di gioco aggiornato:** "
                f"{format_markdown_code(result['previous_game_username'])} → {format_markdown_code(profile.get('game_username'))}"
            )
        if result.get("telegram_username_changed"):
            admin_lines.append(
                "📛 **Username Telegram aggiornato:** "
                f"{format_markdown_code(format_telegram_username(result.get('previous_telegram_username')))} → {format_markdown_code(telegram_username_display)}"
            )
        migrate_result = result.get("migrate_result") or {}
        migrate_status = migrate_result.get("status")
        if migrate_status and migrate_status != "unchanged":
            admin_lines.append(
                f"🗃️ **Migrazione dati utenti:** {format_markdown_code(migrate_status)}"
            )
        verification_payload = result.get("verification")
        if verification_payload:
            method = verification_payload.get("method", "sconosciuta")
            admin_lines.append(
                f"🔐 **Verifica completata via:** {format_markdown_code(method)}"
            )

        self.schedule_admin_notification(
            "\n".join(admin_lines),
            notification_type=NotificationType.SUCCESS,
        )

    async def _fetch_player_by_username(
        self, username: str
    ) -> Optional[Dict[str, Any]]:
        if not username:
            return None
        query = quote_plus(username.strip())
        url = f"https://api.wolvesville.com/players/search?username={query}"
        headers = {
            "Authorization": f"Bot {self.wolvesville_api_key}",
            "Accept": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        self.logger.warning(
                            "Impossibile recuperare il giocatore %s (status %s)",
                            username,
                            response.status,
                        )
                        return None
                    payload = await response.json()
        except Exception as exc:
            self.logger.error(
                "Errore durante la ricerca del giocatore %s: %s", username, exc
            )
            return None

        if isinstance(payload, list):
            return payload[0] if payload else None
        return payload

    @staticmethod
    def _generate_verification_code() -> str:
        return secrets.token_hex(3).upper()

    @staticmethod
    def _build_verification_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Ho aggiornato il profilo",
                        callback_data="link_verify",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Annulla",
                        callback_data="link_cancel",
                    )
                ],
            ]
        )
