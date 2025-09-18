"""Handlers responsible for member browsing and profile lookups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import aiohttp
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class PlayerStates(StatesGroup):
    MEMBER_CHECK = State()
    PROFILE_SEARCH = State()


@dataclass
class MemberSearchHandlers:
    wolvesville_api_key: str
    clan_id: str
    logger: Any

    def __post_init__(self) -> None:
        self.router = Router()
        self.router.callback_query.register(
            self.handle_member_check, F.data.startswith("is_member_")
        )
        self.router.callback_query.register(
            self.handle_navigation, F.data.startswith("navigate_")
        )
        self.router.callback_query.register(
            self.handle_profile_callback, F.data.startswith("profile_")
        )
        self.router.message.register(self.search_profile, PlayerStates.PROFILE_SEARCH)
        self.router.callback_query.register(
            self.show_avatars_callback, F.data.startswith("avatars_")
        )

    async def start_member_question(
        self, message: types.Message, state: FSMContext
    ) -> None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Sì", callback_data="is_member_yes"
                    ),
                    InlineKeyboardButton(text="❌ No", callback_data="is_member_no"),
                ]
            ]
        )
        await message.answer("È un membro del clan?", reply_markup=keyboard)
        await state.set_state(PlayerStates.MEMBER_CHECK)

    async def handle_member_check(
        self, callback: types.CallbackQuery, state: FSMContext
    ) -> None:
        choice = callback.data.split("_")[-1]
        try:
            await callback.message.delete()
        except Exception:
            pass

        if choice == "yes":
            await self._show_clan_members(callback, state)
        else:
            prompt_msg = await callback.message.answer(
                "Inserisci l'username del profilo che vuoi cercare:"
            )
            await state.update_data(username_prompt_msg_id=prompt_msg.message_id)
            await state.set_state(PlayerStates.PROFILE_SEARCH)

    async def handle_navigation(
        self, callback: types.CallbackQuery, state: FSMContext
    ) -> None:
        page_num = int(callback.data.split("_", 1)[1])
        await state.update_data(current_page=page_num)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await self._show_members_page(callback.message, state)

    async def handle_profile_callback(
        self, callback: types.CallbackQuery, state: FSMContext
    ) -> None:
        username = callback.data.split("_", 1)[1]
        self.logger.debug("Ricerca profilo (membro) per username: %s", username)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await self._search_by_username(callback.message, username)

    async def search_profile(self, message: types.Message, state: FSMContext) -> None:
        try:
            data = await state.get_data()
            prompt_msg_id = data.get("username_prompt_msg_id")
            if prompt_msg_id:
                try:
                    await message.bot.delete_message(message.chat.id, prompt_msg_id)
                except Exception:
                    pass

            username = (message.text or "").strip()
            is_valid, error_msg = self._validate_username(username)
            if not is_valid:
                error_message = await message.answer(
                    f"{error_msg}\n\n💡 Riprova inserendo un username valido:"
                )
                await state.update_data(
                    username_prompt_msg_id=error_message.message_id
                )
                return

            self.logger.debug("Ricerca profilo per username: %s", username)
            await self._search_by_username(message, username)
        except Exception as exc:
            self.logger.error("Errore in search_profile: %s", exc)
            await message.answer("❌ Si è verificato un errore. Riprova più tardi.")
        finally:
            try:
                await message.delete()
            except Exception:
                pass
            await state.clear()

    async def show_avatars_callback(self, callback: types.CallbackQuery) -> None:
        _, decision, player_id = callback.data.split("_", 2)
        try:
            await callback.message.edit_reply_markup(None)
        except Exception as exc:
            self.logger.warning("Impossibile rimuovere la tastiera: %s", exc)

        if decision != "yes":
            return

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {self.wolvesville_api_key}",
                    "Accept": "application/json",
                }
                url = f"https://api.wolvesville.com/players/{player_id}"
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        return
                    player_info = await response.json()

            avatars = player_info.get("avatars", [])
            if not avatars:
                return

            for index, avatar in enumerate(avatars):
                letter = chr(65 + index)
                if letter > "X":
                    break
                avatar_url = avatar.get("url", "")
                if not avatar_url:
                    continue
                best_url = await self._get_best_resolution_url(avatar_url)
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(best_url) as image_response:
                            if image_response.status != 200:
                                continue
                            raw = await image_response.read()
                            await callback.message.answer_photo(
                                photo=types.BufferedInputFile(
                                    raw, filename=f"avatar_{letter}.png"
                                ),
                                caption=f"Slot {letter}",
                            )
                except Exception as exc:
                    self.logger.warning(
                        "Errore avatar %s: %s", best_url, exc
                    )
        except Exception as exc:
            self.logger.error("Errore durante l'invio avatar: %s", exc)

    async def _show_clan_members(
        self, callback: types.CallbackQuery, state: FSMContext
    ) -> None:
        try:
            progress_message = await callback.message.answer("Caricamento in corso...")
        except Exception:
            return

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {self.wolvesville_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                url = f"https://api.wolvesville.com/clans/{self.clan_id}/members"
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        await progress_message.delete()
                        await callback.message.answer(
                            f"Impossibile recuperare i membri del clan. (status={response.status})"
                        )
                        return
                    members = await response.json()

            usernames = [member["username"] for member in members if "username" in member]
            await progress_message.delete()
            if not usernames:
                await callback.message.answer("Nessun membro trovato nel clan.")
                return

            pages = [
                usernames[i : i + 10] for i in range(0, len(usernames), 10)
            ]
            await state.update_data(pages=pages, current_page=0)
            await self._show_members_page(callback.message, state)
        except Exception as exc:
            self.logger.error("Errore durante il recupero dei membri: %s", exc)
            await callback.message.answer("Impossibile recuperare i membri del clan!")

    async def _show_members_page(
        self, message: types.Message, state: FSMContext
    ) -> None:
        data = await state.get_data()
        pages = data.get("pages", [])
        current_page = data.get("current_page", 0)
        if not pages:
            await message.answer("Nessun membro trovato nel clan.")
            return

        members = pages[current_page]
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=username, callback_data=f"profile_{username}")]
                for username in members
            ]
        )
        navigation_buttons: List[InlineKeyboardButton] = []
        if current_page > 0:
            navigation_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Indietro", callback_data=f"navigate_{current_page - 1}"
                )
            )
        if current_page < len(pages) - 1:
            navigation_buttons.append(
                InlineKeyboardButton(
                    text="➡️ Avanti", callback_data=f"navigate_{current_page + 1}"
                )
            )
        if navigation_buttons:
            keyboard.inline_keyboard.append(navigation_buttons)

        text_page = f"Pagina {current_page + 1}/{len(pages)}:"
        await message.answer(text_page, reply_markup=keyboard)

    async def _search_by_username(
        self, sender_message: types.Message, username: str
    ) -> None:
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {self.wolvesville_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                url = f"https://api.wolvesville.com/players/search?username={username}"
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        await self._send_not_exists(sender_message, username)
                        return
                    player_data = await response.json()
                    self.logger.debug(
                        "Dati ricevuti per %s: %s", username, player_data
                    )

                if not player_data:
                    await self._send_not_exists(sender_message, username)
                    return

                player_info = player_data[0] if isinstance(player_data, list) else player_data
                if not player_info or "id" not in player_info:
                    await self._send_not_exists(sender_message, username)
                    return

                info_text = self._format_player_info(player_info)
                equipped = player_info.get("equippedAvatar", {})
                equipped_url = equipped.get("url", "")
                avatars = player_info.get("avatars", [])
                has_avatars = len(avatars) > 0

                if equipped_url:
                    eq_url_hd = await self._get_best_resolution_url(equipped_url)
                else:
                    eq_url_hd = ""

                if eq_url_hd:
                    keyboard = None
                    if has_avatars:
                        keyboard = InlineKeyboardMarkup(
                            inline_keyboard=[
                                [
                                    InlineKeyboardButton(
                                        text="👀 Sì, mostra avatar",
                                        callback_data=f"avatars_yes_{player_info['id']}",
                                    ),
                                    InlineKeyboardButton(
                                        text="❌ No",
                                        callback_data=f"avatars_no_{player_info['id']}",
                                    ),
                                ]
                            ]
                        )
                    await sender_message.answer_photo(
                        photo=eq_url_hd,
                        caption=info_text,
                        reply_markup=keyboard,
                    )
                else:
                    if has_avatars:
                        keyboard = InlineKeyboardMarkup(
                            inline_keyboard=[
                                [
                                    InlineKeyboardButton(
                                        text="👀 Sì, mostra avatar",
                                        callback_data=f"avatars_yes_{player_info['id']}",
                                    ),
                                    InlineKeyboardButton(
                                        text="❌ No",
                                        callback_data=f"avatars_no_{player_info['id']}",
                                    ),
                                ]
                            ]
                        )
                        await sender_message.answer(info_text, reply_markup=keyboard)
                    else:
                        await sender_message.answer(info_text)
        except Exception as exc:
            self.logger.error(
                "Errore generico durante la ricerca di %s: %s", username, exc
            )
            await self._send_not_exists(sender_message, username)

    async def _send_not_exists(self, sender_message: types.Message, username: str) -> None:
        await sender_message.answer(f"L'utente {username} non esiste!")

    @staticmethod
    def _validate_username(username: str) -> Tuple[bool, str]:
        if not username:
            return False, "❌ Username non può essere vuoto."
        if len(username) < 3:
            return False, "❌ Username deve essere almeno 3 caratteri."
        if len(username) > 20:
            return False, "❌ Username non può superare 20 caratteri."
        if not all(char.isalnum() or char == "_" for char in username):
            return False, "❌ Username può contenere solo lettere, numeri e underscore (_)."
        return True, ""

    @staticmethod
    def _format_player_info(player_info: Dict[str, Any]) -> str:
        def format_field(value, hidden_text="Nascosto"):
            return hidden_text if value in (-1, None, "N/A") else str(value)

        last_online = player_info.get("lastOnline", "N/A")
        formatted_last_online = last_online.split("T")[0] if "T" in last_online else last_online
        creation_time = player_info.get("creationTime", "N/A")
        formatted_creation_time = (
            creation_time.split("T")[0] if "T" in creation_time else creation_time
        )
        clan_id = player_info.get("clanId", "N/A")
        formatted_clan_id = "Nessuno" if clan_id == "N/A" else clan_id
        game_stats = player_info.get("gameStats", {})

        text_info = (
            f"<b>Informazioni per il giocatore</b> <i>{player_info.get('username', 'N/A')}</i>:\n\n"
            f"<b>ID:</b> {player_info.get('id', 'N/A')}\n"
            f"<b>Messaggio Personale:</b>\n{player_info.get('personalMessage', 'N/A')}\n\n"
            f"<b>Livello:</b> {format_field(player_info.get('level', 'N/A'))}\n"
            f"<b>Stato:</b> {player_info.get('status', 'N/A')}\n"
            f"<b>Ultimo Accesso:</b> {formatted_last_online}\n\n"
            f"<b>Roses:</b>\n"
            f" • Ricevute: {format_field(player_info.get('receivedRosesCount'))}\n"
            f" • Inviate: {format_field(player_info.get('sentRosesCount'))}\n\n"
            f"<b>ID Clan:</b> {formatted_clan_id}\n"
            f"<b>Tempo di Creazione:</b> {formatted_creation_time}\n\n"
            f"<b>Statistiche di Gioco:</b>\n"
            f" • Vittorie Totali: {format_field(game_stats.get('totalWinCount'))}\n"
            f" • Sconfitte Totali: {format_field(game_stats.get('totalLoseCount'))}\n"
            f" • Pareggi Totali: {format_field(game_stats.get('totalTieCount'))}\n"
            f" • Tempo Totale di Gioco (minuti): {format_field(game_stats.get('totalPlayTimeInMinutes'))}\n"
        )
        return text_info

    @staticmethod
    async def _get_best_resolution_url(url_base: str) -> str:
        if not isinstance(url_base, str):
            return ""
        if not url_base.endswith(".png"):
            return url_base

        async with aiohttp.ClientSession() as session:
            url_3x = url_base.replace(".png", "@3x.png")
            async with session.head(url_3x) as response:
                if response.status == 200:
                    return url_3x

        async with aiohttp.ClientSession() as session:
            url_2x = url_base.replace(".png", "@2x.png")
            async with session.head(url_2x) as response:
                if response.status == 200:
                    return url_2x

        return url_base
