"""Menu handlers orchestrating high-level user navigation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

@dataclass
class MenuHandlers:
    bot: Bot
    logger: Any
    mission_flow: Callable[[types.Message, FSMContext], Awaitable[None]]
    balances_view: Callable[[types.Message], Awaitable[None]]
    clan_flow: Callable[[types.Message, FSMContext], Awaitable[None]]
    mission_participants: Callable[[types.Message, FSMContext], Awaitable[None]]
    member_check_flow: Callable[[types.Message, FSMContext], Awaitable[None]]

    def __post_init__(self) -> None:
        self.router = Router()
        self.router.message.register(self.start_command, Command("start"))
        self.router.message.register(self.menu_command, Command("menu"))
        self.router.callback_query.register(
            self.handle_menu_callback, F.data.startswith("menu_")
        )

    async def start_command(self, message: types.Message) -> None:
        keyboard = self._build_menu_keyboard()
        await self._send_and_log("Scegli un'opzione:", message.chat.id, keyboard)
        await self._delete_command_message(message)

    async def menu_command(self, message: types.Message) -> None:
        keyboard = self._build_menu_keyboard()
        await message.answer("Scegli un'opzione:", reply_markup=keyboard)
        await self._delete_command_message(message)

    async def handle_menu_callback(
        self, callback: types.CallbackQuery, state: FSMContext
    ) -> None:
        choice = callback.data.split("_", 1)[-1]
        self.logger.info("Menu callback choice: %s", choice)
        try:
            await callback.message.delete()
        except Exception as exc:
            self.logger.warning("Error deleting message: %s", exc)

        if choice == "player":
            await self.member_check_flow(callback.message, state)
        elif choice == "clan":
            await self.clan_flow(callback.message, state)
        elif choice == "missione":
            await self.mission_flow(callback.message, state)
        elif choice == "balances":
            await self.balances_view(callback.message)
        elif choice == "partecipanti":
            await self.mission_participants(callback.message, state)
        elif choice == "help":
            await callback.message.answer(self._help_text(), parse_mode="HTML")
        else:
            await callback.message.answer("Opzione non riconosciuta.")

    async def _send_and_log(
        self,
        text: str,
        chat_id: int,
        reply_markup: InlineKeyboardMarkup,
    ) -> None:
        self.logger.info("Sending message to %s: %s", chat_id, text)
        await self.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

    def _build_menu_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👤 Giocatore", callback_data="menu_player")],
                [InlineKeyboardButton(text="🏰 Clan", callback_data="menu_clan")],
                [InlineKeyboardButton(text="⏩ Missione", callback_data="menu_missione")],
                [InlineKeyboardButton(text="❓ Help", callback_data="menu_help")],
                [
                    InlineKeyboardButton(text="Bilancio", callback_data="menu_balances"),
                    InlineKeyboardButton(text="Player Missione", callback_data="menu_partecipanti"),
                ],
            ]
        )

    async def _delete_command_message(self, message: types.Message) -> None:
        try:
            if message.chat.type != "private":
                bot_member = await message.chat.get_member(message.bot.id)
                if bot_member.can_delete_messages:
                    await message.delete()
            else:
                await message.delete()
        except Exception as exc:
            self.logger.warning("Cannot delete message: %s", exc)

    @staticmethod
    def _help_text() -> str:
        return """<b>🤖 GUIDA COMPLETA BOT CLAN</b>

<b>📋 FUNZIONI PRINCIPALI</b>

<b>👤 GIOCATORE</b>
🔸 <i>Membro del Clan</i>: Visualizza lista paginata di tutti i membri
🔸 <i>Ricerca Esterna</i>: Cerca qualsiasi giocatore per username
🔸 <i>Profili Completi</i>: Statistiche, livello, clan, avatar
🔸 <i>Avatar Gallery</i>: Visualizza tutti gli avatar del giocatore

<b>🏰 CLAN</b>
🔸 <i>Clan Salvati</i>: Lista dei clan già cercati
🔸 <i>Ricerca Diretta</i>: <code>/clan [ID]</code> per nuove ricerche
🔸 <i>Info Complete</i>: Membri, risorse, statistiche clan

<b>⚔️ MISSIONI</b>
🔸 <i>Skin Disponibili</i>: Visualizza missioni con anteprime
🔸 <i>Skip Timer</i>: Salta tempo di attesa (solo admin)
🔸 <i>Invio Automatico</i>: Ogni lunedì alle 11:00

<b>💰 BILANCIO DONAZIONI</b>
🔸 <i>Calcolo Automatico</i>: Traccia donazioni e costi missioni
🔸 <i>Visualizzazione</i>: Tabella ordinata di tutti i bilanci
🔸 <i>Modifica Admin</i>: Solo amministratori possono modificare
🔸 <i>Gestione Debiti</i>: Notifiche automatiche per uscite con debiti

<b>🎯 ABILITAZIONE MISSIONI</b>
🔸 <i>Voti Automatici</i>: Abilita chi ha votato per una missione
🔸 <i>Gestione Partecipanti</i>: Controllo completo dei partecipanti

<b>🔧 FUNZIONI ADMIN</b>
🔸 <i>Pulizia Database</i>: <code>/cleanup</code> - Rimuove duplicati
🔸 <i>Controllo Uscite</i>: Monitora membri usciti dal clan
🔸 <i>Gestione Automatica</i>: Sistema scheduler per manutenzione

<b>🔄 AUTOMAZIONI</b>
🔸 <i>Ledger Donazioni</i>: Aggiornamento ogni 5 minuti
🔸 <i>Missioni Attive</i>: Calcolo costi ogni 5 minuti
🔸 <i>Membri Clan</i>: Sincronizzazione ogni 3 giorni
🔸 <i>Pulizia DB</i>: Rimozione duplicati ogni 24 ore
🔸 <i>Controllo Uscite</i>: Verifica debiti ogni 6 ore

<b>💡 SUGGERIMENTI</b>
• Usa <code>/start</code> o <code>/menu</code> per navigare
• I comandi admin richiedono autorizzazione
• Le modifiche ai bilanci sono tracciate automaticamente
• Il bot mantiene cronologia delle ricerche clan"""
