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
        self.router.message.register(self.help_command, Command("help"))
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

    async def help_command(self, message: types.Message) -> None:
        await message.answer(self._help_text(), parse_mode="HTML")

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
        sections = [
            "<b>🤖 GUIDA COMPLETA BOT CLAN</b>",
            "",
            "<b>📌 NAVIGAZIONE RAPIDA</b>",
            "• <code>/start</code> – Avvia il bot e apre il menu principale interattivo",
            "• <code>/menu</code> – Richiama in qualsiasi momento le scorciatoie più utilizzate",
            "• <code>/help</code> – Elenco completo e sempre aggiornato delle funzionalità disponibili",
            "",
            "<b>🏆 SISTEMA RICOMPENSE</b>",
            "• <code>/classifica [periodo]</code> – Classifica dinamica dei punti premio (Top 10).",
            "  <i>Periodi supportati:</i> <code>totale</code>, <code>settimana</code>, <code>mese</code>, <code>oggi</code> e sinonimi.",
            "  <i>Dettagli inclusi:</i> punti del periodo, totale storico e icone degli achievement sbloccati.",
            "• <code>/progressi &lt;username&gt; [periodo]</code> – Scheda avanzata di un giocatore.",
            "  <i>Mostra:</i> punteggio complessivo, andamento nel periodo scelto, distribuzione per tipologia, ultimi eventi registrati e achievement ottenuti.",
            "• <i>Classifiche automatiche</i> – Aggiornamenti settimanali e mensili inviati in automatico agli amministratori via notifica.",
            "• <i>Notifiche achievement</i> – Ogni traguardo attiva un alert dedicato con riepilogo e bonus punti accreditati.",
            "",
            "<b>👤 GESTIONE GIOCATORI</b>",
            "• <i>Membro del Clan</i> – Elenco paginato con dati di profilo, stato online e attività recenti.",
            "• <i>Ricerca Esterna</i> – Trova qualsiasi giocatore partendo dallo username Wolvesville.",
            "• <i>Profili Completi</i> – Statistiche, livello, clan di appartenenza e galleria avatar sempre aggiornata.",
            "• <code>/collega</code> – Collega il profilo Telegram a quello di gioco per sbloccare funzioni avanzate e sincronizzazioni automatiche.",
            "• <code>/membri</code> – Elenco aggiornato del clan con nomi Telegram, tag e stato del collegamento.",
            "",
            "<b>🧭 COME COLLEGARE TELEGRAM A WOLVESVILLE</b>",
            "1. Apri la chat privata con il bot e invia <code>/collega</code>.",
            "2. Inserisci l'username di gioco esattamente come appare in Wolvesville.",
            "3. Copia il codice generato nel tuo messaggio personale dell'app.",
            "4. Premi «Ho aggiornato il profilo» per far verificare automaticamente il collegamento.",
            "5. Una volta verificato, la lista membri e le statistiche si aggiorneranno in autonomia.",
            "",
            "<b>🏰 STRUMENTI CLAN</b>",
            "• <code>/clan [ID]</code> – Dossier completo su qualsiasi clan (membri, progressi, attività recenti).",
            "• <i>Clan salvati</i> – Accesso rapido alle ricerche più frequenti effettuate dal bot.",
            "• <i>Statistiche</i> – Analisi delle risorse condivise, andamento membri e confronto con i periodi precedenti.",
            "",
            "<b>⚔️ MISSIONI</b>",
            "• <i>Skin disponibili</i> – Dettagli missione con immagini, costi e ricompense.",
            "• <i>Partecipanti</i> – Monitoraggio live dal menu «Player Missione».",
            "• <i>Skip timer</i> – Riduzione del tempo di attesa (riservata agli admin autorizzati).",
            "• <i>Supporto missioni</i> – Nuovo sistema premi per chi assiste le squadre durante i raid.",
            "",
            "<b>💰 ECONOMIA</b>",
            "• <code>/balances</code> – Bilancio donazioni suddiviso per valuta e giocatore.",
            "• <i>Calcoli automatici</i> – Donazioni, costi missione e debiti gestiti in tempo reale.",
            "• <i>Ledger cronologico</i> – Storico contributi oro/gemme integrato con il sistema ricompense.",
            "",
            "<b>🔧 COMANDI ADMIN</b>",
            "• <code>/cleanup</code> – Rimuove duplicati e sincronizza i dati tra le diverse collezioni MongoDB.",
            "• <i>Controllo uscite</i> – Notifiche automatiche per chi lascia il clan con debiti pendenti.",
            "• <i>Monitoraggio gruppi</i> – Alert immediati quando il bot entra in chat non autorizzate (con blacklist automatica).",
            "",
            "<b>🔄 AUTOMAZIONI</b>",
            "• Ledger donazioni ogni 5 minuti",
            "• Calcolo missioni attive ogni 5 minuti",
            "• Sincronizzazione profili collegati ogni intervallo configurato",
            "• Pulizia database ogni 24 ore",
            "• Controllo uscite ogni 6 ore",
            "• Classifiche reward settimanali e mensili inviate automaticamente agli admin",
            "",
            "<b>💡 SUGGERIMENTI</b>",
            "• Usa il menu rapido per avviare i flussi guidati principali.",
            "• Specifica il periodo quando utilizzi <code>/classifica</code> o <code>/progressi</code> per filtrare i risultati.",
            "• Gli achievement sbloccati garantiscono punti bonus immediati registrati nello storico premi.",
            "• I comandi admin richiedono autorizzazioni dedicate: contatta il responsabile del clan per l'abilitazione.",
        ]
        return "\n".join(sections)
