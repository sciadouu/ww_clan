"""Handlers dedicated to clan lookups and saved clan navigation."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


CLAN_DATA_FILE = "clan_data.json"


def load_saved_clans() -> List[Dict[str, str]]:
    if not os.path.exists(CLAN_DATA_FILE):
        return []
    try:
        with open(CLAN_DATA_FILE, "r", encoding="utf-8") as fp:
            data = json.load(fp)
            return data.get("clans", [])
    except Exception:
        return []


def save_saved_clans(clans: List[Dict[str, str]]) -> None:
    data = {"clans": clans}
    with open(CLAN_DATA_FILE, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def add_clan_to_file(clan_id: str, clan_name: str) -> None:
    clans = load_saved_clans()
    if any(clan.get("id") == clan_id for clan in clans):
        return
    clans.append({"id": clan_id, "name": clan_name})
    save_saved_clans(clans)


@dataclass
class ClanHandlers:
    wolvesville_api_key: str
    logger: Any

    def __post_init__(self) -> None:
        self.router = Router()
        self.router.message.register(self.clan_command, Command("clan"))
        self.router.callback_query.register(
            self.handle_clan_callback, F.data.startswith("clan_")
        )

    async def clan_command(self, message: types.Message) -> None:
        try:
            args = (message.text or "").strip().split()
            if len(args) < 2:
                await message.answer(
                    "❌ Specifica l'ID del clan.\n📝 Uso: /clan <clanId>\n📋 Esempio: /clan 12345"
                )
                return

            clan_id = args[1].strip()
            if not await self._validate_clan_id(clan_id, message):
                return

            clan_info = await self._fetch_clan_info(clan_id)
            if not clan_info:
                await message.answer("❌ Clan non trovato. Verifica l'ID e riprova.")
                return

            clan_name = clan_info.get("name", "Sconosciuto")
            add_clan_to_file(clan_id, clan_name)

            response_text = (
                f"<b>Informazioni sul Clan:</b>\n"
                f"ID: {clan_id}\n"
                f"Nome: {clan_name}\n"
                f"Descrizione: {clan_info.get('description', 'N/A')}\n"
                f"XP: {clan_info.get('xp', 'N/A')}\n"
                f"Lingua: {clan_info.get('language', 'N/A')}\n"
                f"Tag: {clan_info.get('tag', 'N/A')}\n"
                f"Tipo di Unione: {clan_info.get('joinType', 'N/A')}\n"
                f"ID Leader: {clan_info.get('leaderId', 'N/A')}\n"
                f"Conteggio quest: {clan_info.get('questHistoryCount', 'N/A')}\n"
                f"Livello Minimo: {clan_info.get('minLevel', 'N/A')}\n"
                f"Membri: {clan_info.get('memberCount', 'N/A')}\n"
                f"Oro: {clan_info.get('gold', 'N/A')}\n"
                f"Gemme: {clan_info.get('gems', 'N/A')}\n"
            )
            await message.answer(response_text)
        except aiohttp.ClientError as exc:
            self.logger.error("Errore di rete /clan %s => %s", clan_id, exc)
            await message.answer("❌ Errore di connessione. Riprova più tardi.")
        except Exception as exc:
            self.logger.error("Errore generico /clan %s => %s", clan_id, exc)
            await message.answer("❌ Si è verificato un errore. Riprova più tardi.")

    async def start_saved_clan_flow(
        self, message: types.Message, state: FSMContext
    ) -> None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Sì", callback_data="clan_si"),
                              InlineKeyboardButton(text="No", callback_data="clan_no")]]
        )
        await message.answer("Ciao. Vuoi visualizzare i clan salvati?", reply_markup=keyboard)

    async def handle_clan_callback(
        self, callback: types.CallbackQuery, state: FSMContext
    ) -> None:
        parts = callback.data.split("_", 2)

        try:
            await callback.message.delete()
        except Exception:
            pass

        if len(parts) == 3 and parts[1] == "show":
            clan_id = parts[2]
            clan_info = await self._fetch_clan_info(clan_id)
            if not clan_info:
                await callback.message.answer(
                    "Impossibile recuperare le info del clan!"
                )
                return

            clan_name = clan_info.get("name", "Sconosciuto")
            response_text = (
                f"<b>Informazioni sul Clan</b>\n\n"
                f"<b>Nome:</b> {clan_name}\n"
                f"<b>Descrizione:</b> {clan_info.get('description', 'N/A')}\n"
                f"<b>XP:</b> {clan_info.get('xp', 'N/A')}\n"
                f"<b>Lingua:</b> {clan_info.get('language', 'N/A')}\n"
                f"<b>Tag:</b> {clan_info.get('tag', 'N/A')}\n"
                f"<b>Membri:</b> {clan_info.get('memberCount', 'N/A')}\n"
                f"<b>Oro:</b> {clan_info.get('gold', 'N/A')}\n"
                f"<b>Gemme:</b> {clan_info.get('gems', 'N/A')}"
            )
            await callback.message.answer(response_text, parse_mode="HTML")
        elif parts[1] == "si":
            saved_clans = load_saved_clans()
            if not saved_clans:
                await callback.message.answer("Non ci sono clan salvati.")
                return

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=clan.get("name", clan.get("id", "")),
                            callback_data=f"clan_show_{clan['id']}",
                        )
                    ]
                    for clan in saved_clans
                ]
            )
            await callback.message.answer(
                "Ecco i clan salvati. Seleziona per vedere i dettagli:",
                reply_markup=keyboard,
            )
        else:
            await callback.message.answer(
                "Ok! Puoi usare /clan <id> per cercare un nuovo clan."
            )

    async def _fetch_clan_info(self, clan_id: str) -> Optional[Dict[str, Any]]:
        url = f"https://api.wolvesville.com/clans/{clan_id}/info"
        headers = {
            "Authorization": f"Bot {self.wolvesville_api_key}",
            "Accept": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            resp = await session.get(url, headers=headers)
            if resp.status != 200:
                return None
            return await resp.json()

    async def _validate_clan_id(self, clan_id: str, message: types.Message) -> bool:
        if not clan_id:
            await message.answer("❌ ID clan non può essere vuoto.")
            return False
        if len(clan_id) < 5 or len(clan_id) > 50:
            await message.answer("❌ ID clan deve essere tra 5 e 50 caratteri.")
            return False
        if not all(char.isalnum() or char in "-_" for char in clan_id):
            await message.answer(
                "❌ ID clan può contenere solo lettere, numeri, trattini e underscore."
            )
            return False
        return True
