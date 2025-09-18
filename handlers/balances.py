"""Routers dedicated to donation balance management flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Sequence

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.db_manager import MongoManager


class ModifyStates(StatesGroup):
    CHOOSING_PLAYER = State()
    CHOOSING_CURRENCY = State()
    ENTERING_AMOUNT = State()


@dataclass
class BalancesHandlers:
    db_manager: MongoManager
    bot: Bot
    admin_ids: Sequence[int]
    logger: Any

    def __post_init__(self) -> None:
        self._admin_set = set(self.admin_ids)
        self.router = Router()
        self.router.message.register(self.show_balances_command, Command("balances"))
        self.router.callback_query.register(
            self.close_balances_callback, F.data == "close_balances"
        )
        self.router.callback_query.register(
            self.modify_start, F.data == "modify_start"
        )
        self.router.callback_query.register(
            self.modify_paginate, F.data.startswith("modify_paginate_")
        )
        self.router.callback_query.register(
            self.modify_choose_player, F.data.startswith("modify_player_")
        )
        self.router.callback_query.register(
            self.modify_choose_currency, F.data.startswith("modify_currency_")
        )
        self.router.message.register(
            self.modify_enter_amount, ModifyStates.ENTERING_AMOUNT
        )
        self.router.callback_query.register(
            self.modify_finish, F.data == "modify_finish"
        )

    async def show_balances_command(self, message: types.Message) -> None:
        await self.show_balances(message)

    async def show_balances(self, message: types.Message) -> None:
        users = await self.db_manager.list_users()
        self.logger.info("Sto per costruire la tabella bilanci. Ecco i documenti dal DB:")
        for doc in users:
            self.logger.info("Doc utente: %s", doc)

        lines: List[str] = [
            "Utente           Oro     Gem",
            "-----------------------------",
        ]
        for doc in users:
            username = doc.get("username", "Sconosciuto")
            donations = doc.get("donazioni", {})
            oro = donations.get("Oro", 0)
            gem = donations.get("Gem", 0)
            lines.append(f"{username:<15}{oro:<8}{gem}")

        text = "<b>Bilanci Donazioni</b>\n\n" "<pre>\n" + "\n".join(lines) + "\n</pre>"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Modifica", callback_data="modify_start")],
                [InlineKeyboardButton(text="Chiudi", callback_data="close_balances")],
            ]
        )
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

    async def close_balances_callback(self, callback: types.CallbackQuery) -> None:
        try:
            await callback.message.delete()
        except Exception as exc:  # pragma: no cover - logging difensivo
            self.logger.error("Errore nella chiusura del messaggio: %s", exc)

    async def modify_start(self, callback: types.CallbackQuery, state: FSMContext) -> None:
        if not await self._check_admin_access(callback):
            return

        try:
            players: List[str] = []
            users = await self.db_manager.list_users()
            for doc in users:
                username = doc.get("username", "Sconosciuto")
                if username != "Sconosciuto":
                    players.append(username)

            if not players:
                await callback.message.answer("❌ Nessun giocatore trovato nel database.")
                return

            await state.update_data(players=players, current_page=0, modify_msg_ids=[])
            keyboard = self._create_players_keyboard(players, page=0)
            text_page = self._make_page_text(0, players, page_size=10)
            msg = await callback.message.answer(text_page, reply_markup=keyboard)
            await self._add_modify_msg(state, msg)
            await state.set_state(ModifyStates.CHOOSING_PLAYER)
        except Exception as exc:
            self.logger.error("Errore in modify_start: %s", exc)
            await callback.message.answer(
                "❌ Si è verificato un errore nell'avvio della modifica."
            )

    async def modify_paginate(self, callback: types.CallbackQuery, state: FSMContext) -> None:
        new_page = int(callback.data.split("_")[-1])
        data = await state.get_data()
        players = data.get("players", [])
        if not players:
            await callback.answer("Nessun giocatore in memoria.")
            return

        await state.update_data(current_page=new_page)
        keyboard = self._create_players_keyboard(players, page=new_page)
        text_page = self._make_page_text(new_page, players, page_size=10)
        await callback.message.edit_text(text_page, reply_markup=keyboard)

    async def modify_choose_player(self, callback: types.CallbackQuery, state: FSMContext) -> None:
        username = callback.data.split("modify_player_")[-1]
        await state.update_data(chosen_player=username)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Gold", callback_data="modify_currency_Gold"
                    ),
                    InlineKeyboardButton(
                        text="Gem", callback_data="modify_currency_Gem"
                    ),
                ],
                [
                    InlineKeyboardButton(text="Indietro", callback_data="modify_start"),
                    InlineKeyboardButton(text="Fine", callback_data="modify_finish"),
                ],
            ]
        )
        msg = await callback.message.answer(
            f"Hai scelto <b>{username}</b>. Seleziona la valuta da modificare:",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        await self._add_modify_msg(state, msg)
        await state.set_state(ModifyStates.CHOOSING_CURRENCY)

    async def modify_choose_currency(
        self, callback: types.CallbackQuery, state: FSMContext
    ) -> None:
        currency = callback.data.split("modify_currency_")[-1]
        if currency.lower() == "gold":
            db_key = "Oro"
        elif currency.lower() == "gem":
            db_key = "Gem"
        else:
            db_key = currency

        await state.update_data(chosen_currency=currency, chosen_db_key=db_key)

        msg = await callback.message.answer(
            f"Inserisci la nuova quantità di <b>{currency}</b>:",
            parse_mode="HTML",
        )
        await self._add_modify_msg(state, msg)
        await state.set_state(ModifyStates.ENTERING_AMOUNT)

    async def modify_enter_amount(
        self, message: types.Message, state: FSMContext
    ) -> None:
        try:
            amount_text = (message.text or "").strip()
            if not amount_text:
                await message.answer("❌ Inserisci un valore numerico.\n💡 Esempio: 1000")
                return

            try:
                new_amount = int(amount_text)
            except ValueError:
                await message.answer(
                    "❌ Il valore deve essere un numero intero.\n💡 Esempi validi: 500, 1000, -200"
                )
                return

            if new_amount < -999_999 or new_amount > 999_999:
                await message.answer(
                    "❌ Il valore deve essere tra -999,999 e 999,999."
                )
                return

            data = await state.get_data()
            username = data.get("chosen_player")
            currency = data.get("chosen_currency")
            db_key = data.get("chosen_db_key")

            if not username or not currency or not db_key:
                await message.answer(
                    "❌ Errore nei dati di sessione. Riprova dall'inizio."
                )
                await state.clear()
                return

            await self.db_manager.set_user_currency(username, db_key, new_amount)

            msg = await message.answer(
                f"✅ {currency} di <b>{username}</b> aggiornato a: <b>{new_amount:,}</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔙 Indietro",
                                callback_data=f"modify_currency_{currency}",
                            ),
                            InlineKeyboardButton(
                                text="✅ Fine", callback_data="modify_finish"
                            ),
                        ]
                    ]
                ),
            )
            await self._add_modify_msg(state, msg)
            await state.set_state(ModifyStates.CHOOSING_CURRENCY)
        except Exception as exc:
            self.logger.error("Errore in modify_enter_amount: %s", exc)
            await message.answer("❌ Si è verificato un errore. Riprova più tardi.")
        finally:
            try:
                await message.delete()
            except Exception:
                pass

    async def modify_finish(self, callback: types.CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        msg_ids = data.get("modify_msg_ids", [])
        chat_id = callback.message.chat.id
        for mid in msg_ids:
            try:
                await self.bot.delete_message(chat_id, mid)
            except Exception as exc:  # pragma: no cover
                self.logger.warning(
                    "Errore nel cancellare il messaggio %s: %s", mid, exc
                )
        await state.clear()

    async def _check_admin_access(self, callback: types.CallbackQuery) -> bool:
        if callback.from_user.id not in self._admin_set:
            await callback.answer(
                "❌ Non hai le autorizzazioni per questa operazione",
                show_alert=True,
            )
            return False
        return True

    async def _add_modify_msg(self, state: FSMContext, msg: types.Message) -> None:
        data = await state.get_data()
        msg_ids = data.get("modify_msg_ids", [])
        msg_ids.append(msg.message_id)
        await state.update_data(modify_msg_ids=msg_ids)

    @staticmethod
    def _create_players_keyboard(
        players: Sequence[str], page: int, page_size: int = 10
    ) -> InlineKeyboardMarkup:
        start_index = page * page_size
        end_index = start_index + page_size
        page_players = players[start_index:end_index]
        kb_buttons = [
            [
                InlineKeyboardButton(
                    text=username, callback_data=f"modify_player_{username}"
                )
            ]
            for username in page_players
        ]
        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Indietro", callback_data=f"modify_paginate_{page-1}"
                )
            )
        if end_index < len(players):
            nav_buttons.append(
                InlineKeyboardButton(
                    text="➡️ Avanti", callback_data=f"modify_paginate_{page+1}"
                )
            )
        if nav_buttons:
            kb_buttons.append(nav_buttons)
        kb_buttons.append([InlineKeyboardButton(text="Fine", callback_data="modify_finish")])
        return InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    @staticmethod
    def _make_page_text(page: int, players: Sequence[str], page_size: int = 10) -> str:
        total_pages = (len(players) - 1) // page_size + 1 if players else 1
        start_index = page * page_size
        end_index = min(start_index + page_size, len(players))
        page_players = players[start_index:end_index]
        text = f"Pagina {page + 1}/{total_pages}:\n" + "\n".join(page_players)
        return text
